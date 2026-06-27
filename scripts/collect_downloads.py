import os, re, time, random
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
MAPPING_FILE = DATA / 'naver_webtoon_series_productNo_mapping_v2_title_cleaned.csv'
HISTORY_CSV = DATA / 'series_download_snapshot_history.csv'
HISTORY_XLSX = DATA / 'series_download_snapshot_history.xlsx'
INCREASE_HISTORY_CSV = DATA / 'series_download_increase_report_history.csv'
INCREASE_HISTORY_XLSX = DATA / 'series_download_increase_report_history.xlsx'
DEDUP_CSV = DATA / 'naver_webtoon_series_mapping_dedup_cleaned.csv'
DEDUP_XLSX = DATA / 'naver_webtoon_series_mapping_dedup_cleaned.xlsx'
KST = ZoneInfo('Asia/Seoul')
HEADLESS = True
MANUAL_SNAPSHOT_WEEKDAY = os.getenv('MANUAL_SNAPSHOT_WEEKDAY', '').strip() or None

WEEKDAYS = ['mon','tue','wed','thu','fri','sat','sun']
PY_TO_EN = {0:'mon',1:'tue',2:'wed',3:'thu',4:'fri',5:'sat',6:'sun'}
KO_TO_EN = {'월':'mon','화':'tue','수':'wed','목':'thu','금':'fri','토':'sat','일':'sun'}
EN_TO_KO = {'mon':'월','tue':'화','wed':'수','thu':'목','fri':'금','sat':'토','sun':'일'}
PREV = {'mon':'sun','tue':'mon','wed':'tue','thu':'wed','fri':'thu','sat':'fri','sun':'sat'}


def get_sheets_service():
    """
    GitHub Secrets의 GOOGLE_SHEETS_ID, GOOGLE_SERVICE_ACCOUNT_JSON을 사용해
    Google Sheets API service 객체를 생성합니다.

    둘 중 하나라도 없으면 Google Sheets 동기화는 건너뛰고,
    기존처럼 GitHub의 data 파일만 저장합니다.
    """
    if not GOOGLE_SHEETS_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        print("Google Sheets settings are missing. Skip Google Sheets sync.")
        return None

    try:
        service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    except json.JSONDecodeError as e:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. "
            "GitHub Secrets에 서비스 계정 JSON 전체를 그대로 붙여넣었는지 확인하세요."
        ) from e

    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=SHEETS_SCOPES,
    )

    return build("sheets", "v4", credentials=credentials)


def get_existing_sheet_names(service, spreadsheet_id):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def ensure_sheet_exists(service, spreadsheet_id, sheet_name):
    """시트 탭이 없으면 생성합니다. Google Sheet 파일 자체는 미리 만들어져 있어야 합니다."""
    if sheet_name in get_existing_sheet_names(service, spreadsheet_id):
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": sheet_name
                        }
                    }
                }
            ]
        },
    ).execute()

    print(f"Created Google Sheet tab: {sheet_name}")


def clear_sheet(service, spreadsheet_id, sheet_name):
    ensure_sheet_exists(service, spreadsheet_id, sheet_name)
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:ZZ",
        body={},
    ).execute()


def prepare_df_for_sheet(df):
    """
    Google Sheets 업로드용으로 DataFrame을 안전하게 변환합니다.
    - NaN/None -> 빈 문자열
    - datetime/Timestamp -> 문자열
    - 나머지는 문자열 처리
    """
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    out = out.replace({pd.NA: ""})
    out = out.fillna("")

    for col in out.columns:
        out[col] = out[col].apply(
            lambda x: x.strftime("%Y-%m-%d %H:%M:%S") if hasattr(x, "strftime") else x
        )

    return out.astype(str)


def ensure_sheet_header(service, spreadsheet_id, sheet_name, columns):
    """시트 첫 행에 헤더가 없으면 헤더를 입력합니다. 탭이 없으면 자동 생성합니다."""
    ensure_sheet_exists(service, spreadsheet_id, sheet_name)

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:ZZ1",
    ).execute()

    values = result.get("values", [])

    if values:
        return

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": [columns]},
    ).execute()


def get_sheet_header(service, spreadsheet_id, sheet_name):
    ensure_sheet_exists(service, spreadsheet_id, sheet_name)

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:ZZ1",
    ).execute()

    values = result.get("values", [])
    return values[0] if values else []


def set_sheet_header(service, spreadsheet_id, sheet_name, columns):
    ensure_sheet_exists(service, spreadsheet_id, sheet_name)

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": [columns]},
    ).execute()


def append_df_to_sheet(service, spreadsheet_id, sheet_name, df):
    """
    DataFrame을 Google Sheets 특정 탭 아래쪽에 누적 append합니다.

    v8 수정:
    기존 탭 헤더와 이번 DataFrame 컬럼이 달라도 값이 밀리지 않도록
    기존 헤더 + 신규 컬럼 순서로 헤더를 확장한 뒤,
    DataFrame도 같은 컬럼 순서로 재정렬해서 append합니다.
    """
    if service is None:
        return

    if df is None or df.empty:
        print(f"No data to append: {sheet_name}")
        return

    df_to_upload = prepare_df_for_sheet(df)
    incoming_columns = list(df_to_upload.columns)

    existing_header = get_sheet_header(service, spreadsheet_id, sheet_name)

    if not existing_header:
        final_columns = incoming_columns
        set_sheet_header(service, spreadsheet_id, sheet_name, final_columns)
    else:
        final_columns = list(existing_header)
        for c in incoming_columns:
            if c not in final_columns:
                final_columns.append(c)

        if final_columns != existing_header:
            set_sheet_header(service, spreadsheet_id, sheet_name, final_columns)
            print(f"Expanded Google Sheet header for {sheet_name}: {len(existing_header)} -> {len(final_columns)} columns")

    for c in final_columns:
        if c not in df_to_upload.columns:
            df_to_upload[c] = ""

    df_to_upload = df_to_upload[final_columns]
    values = df_to_upload.values.tolist()

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()

    print(f"Appended {len(df_to_upload)} rows to Google Sheet: {sheet_name}")


def replace_df_to_sheet(service, spreadsheet_id, sheet_name, df):
    """
    DataFrame으로 Google Sheets 특정 탭을 통째로 교체합니다.
    dedup_mapping처럼 매일 누적하면 중복이 쌓이는 탭에 사용합니다.
    """
    if service is None:
        return

    if df is None or df.empty:
        print(f"No data to replace: {sheet_name}")
        return

    df_to_upload = prepare_df_for_sheet(df)
    columns = list(df_to_upload.columns)
    values = [columns] + df_to_upload.values.tolist()

    clear_sheet(service, spreadsheet_id, sheet_name)

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    print(f"Replaced Google Sheet tab with {len(df_to_upload)} rows: {sheet_name}")


def sync_google_sheets(snapshot_df, increase_report_df, dedup_mapping_df, full_increase_report_df=None):
    """
    수집 결과를 Google Sheets에 반영합니다.

    append 방식:
    - snapshot_history: 이번 실행분 snapshot_df만 append
    - problem_rows: 이번 실행 중 download_status != ok인 행만 append

    replace 방식:
    - increase_report: 전체 snapshot_history 기준으로 다시 계산한 증가량 리포트로 교체
      v8 핵심 수정. 기존 increase_report 탭의 구버전 헤더 때문에 값이 밀리는 문제를 제거합니다.
    - dedup_mapping: 매일 최신 매핑으로 전체 교체
    """
    service = get_sheets_service()

    if service is None:
        return

    # 1. 이번 실행 스냅샷만 누적
    append_df_to_sheet(
        service,
        GOOGLE_SHEETS_ID,
        "snapshot_history",
        snapshot_df,
    )

    # 2. 증가량 리포트는 누적 append하지 않고 전체 재계산본으로 교체
    report_to_sheet = full_increase_report_df if full_increase_report_df is not None else increase_report_df
    replace_df_to_sheet(
        service,
        GOOGLE_SHEETS_ID,
        "increase_report",
        report_to_sheet,
    )

    # 3. 이번 실행 중 수집 실패/검수 필요 행만 누적
    if snapshot_df is not None and not snapshot_df.empty and "download_status" in snapshot_df.columns:
        problem_df = snapshot_df[snapshot_df["download_status"] != "ok"].copy()
        append_df_to_sheet(
            service,
            GOOGLE_SHEETS_ID,
            "problem_rows",
            problem_df,
        )

    # 4. 작품 매핑은 누적하지 않고 매일 최신 상태로 교체
    replace_df_to_sheet(
        service,
        GOOGLE_SHEETS_ID,
        "dedup_mapping",
        dedup_mapping_df,
    )

def now_kst(): return datetime.now(KST)
def snapshot_id(): return now_kst().strftime('%Y%m%d_%H%M%S')
def today_weekday(): return MANUAL_SNAPSHOT_WEEKDAY or PY_TO_EN[now_kst().weekday()]

def norm_day(v):
    if v is None: return ''
    s = str(v).strip().lower()
    m = {'월':'mon','월요일':'mon','mon':'mon','monday':'mon','화':'tue','화요일':'tue','tue':'tue','tuesday':'tue','수':'wed','수요일':'wed','wed':'wed','wednesday':'wed','목':'thu','목요일':'thu','thu':'thu','thursday':'thu','금':'fri','금요일':'fri','fri':'fri','friday':'fri','토':'sat','토요일':'sat','sat':'sat','saturday':'sat','일':'sun','일요일':'sun','sun':'sun','sunday':'sun'}
    return m.get(s, s)

def ordered(days):
    found=[]
    for d in days:
        d=norm_day(d)
        if d in WEEKDAYS and d not in found: found.append(d)
    return [d for d in WEEKDAYS if d in found]

def parse_days(v):
    if v is None: return []
    s=str(v).strip()
    if not s or s.lower() in ['nan','none','null']: return []
    if '매일' in s: return WEEKDAYS[:]
    if ',' in s:
        ds=[norm_day(x.strip()) for x in s.split(',') if x.strip()]
        ds=[x for x in ds if x in WEEKDAYS]
        if ds: return ordered(ds)
    ds=[]
    for ko,en in KO_TO_EN.items():
        if ko in s: ds.append(en)
    if ds: return ordered(ds)
    one=norm_day(s)
    return [one] if one in WEEKDAYS else []

def series_url(product_no):
    if product_no is None: return ''
    p=re.sub(r'\.0$','',str(product_no).strip())
    if not p or p.lower()=='nan': return ''
    return f'https://series.naver.com/comic/detail.series?productNo={p}'

def parse_download(v):
    if v is None: return None
    s=str(v).strip().replace(',','').replace('다운로드','').replace('회','').strip()
    if not s or s in ['연재본 보기','보기','소장','대여','무료']: return None
    for unit,mul in [('억',100000000),('만',10000),('천',1000)]:
        m=re.search(r'([\d.]+)\s*'+unit,s)
        if m: return int(float(m.group(1))*mul)
    m=re.search(r'\d+',s)
    return int(m.group()) if m else None

def clean_author(v):
    raw='' if v is None else str(v).strip()
    if not raw or raw.lower() in ['nan','none','null']: return ''
    parts=[p.strip() for p in raw.split('|') if p.strip()]
    if len(parts)>=2 and parts[0].startswith('평점'): return parts[1]
    filt=[]
    for p in parts:
        if p.startswith('평점'): continue
        if re.search(r'\d{4}\.\d{2}\.\d{2}',p): continue
        if re.search(r'총\s*\d+\s*화',p): continue
        if '무료' in p: continue
        filt.append(p)
    return filt[0] if filt else raw

def title_info(v):
    raw='' if v is None else str(v).strip()
    clean=raw; info=''; total=None; status=''
    m=re.search(r'\(\s*(총\s*\d+\s*화\s*/\s*(?:미완결|완결))\s*\)', raw)
    if m:
        info=m.group(1).strip(); clean=re.sub(r'\(\s*총\s*\d+\s*화\s*/\s*(?:미완결|완결)\s*\)','',raw).strip()
    else:
        m=re.search(r'총\s*\d+\s*화\s*/\s*(?:미완결|완결)', raw)
        if m:
            info=m.group(0).strip(); clean=re.sub(r'총\s*\d+\s*화\s*/\s*(?:미완결|완결)','',raw).strip()
    if info:
        cm=re.search(r'총\s*(\d+)\s*화',info)
        if cm: total=int(cm.group(1))
        status='미완결' if '미완결' in info else ('완결' if '완결' in info else '')
    return clean, info, total, status

def first_valid(vals):
    for v in vals:
        if pd.isna(v): continue
        s=str(v).strip()
        if s and s.lower() not in ['nan','none','null']: return v
    return ''

def load_mapping():
    if not MAPPING_FILE.exists(): raise FileNotFoundError(MAPPING_FILE)
    df=pd.read_csv(MAPPING_FILE)
    if 'series_product_no' not in df.columns: df['series_product_no']=''
    if 'series_url' not in df.columns: df['series_url']=''
    df['series_product_no']=df['series_product_no'].fillna('').astype(str).str.replace(r'\.0$','',regex=True).str.strip()
    df['series_url']=df['series_url'].fillna('').astype(str).str.strip()
    mask=df['series_url'].eq('') & df['series_product_no'].ne('')
    df.loc[mask,'series_url']=df.loc[mask,'series_product_no'].apply(series_url)
    if 'weekday' in df.columns: df['weekday']=df['weekday'].apply(norm_day)
    else: df['weekday']=''
    if 'serial_weekdays' not in df.columns: df['serial_weekdays']=''
    if 'series_author' in df.columns:
        df['series_author_raw']=df.get('series_author_raw',df['series_author'])
        df['series_author']=df['series_author_raw'].apply(clean_author)
    if 'series_title' in df.columns:
        df['series_title_raw']=df.get('series_title_raw',df['series_title'])
        parsed=df['series_title_raw'].apply(lambda x: pd.Series(title_info(x),index=['series_title','series_episode_info','series_total_episode_count','series_completion_status']))
        for c in parsed.columns: df[c]=parsed[c]
    return df

def dedup(df):
    key='webtoon_title_id' if 'webtoon_title_id' in df.columns else ('series_product_no' if 'series_product_no' in df.columns else 'webtoon_title')
    df=df.copy(); df['_key']=df[key].fillna('').astype(str).str.replace(r'\.0$','',regex=True).str.strip()
    empty=df['_key'].eq('')|df['_key'].str.lower().isin(['nan','none','null'])
    df.loc[empty,'_key']=df.loc[empty,'webtoon_title'].astype(str)
    reps=[c for c in ['weekday','rank','webtoon_title','webtoon_author','webtoon_title_id','webtoon_url','series_product_no','series_title','series_title_raw','series_episode_info','series_total_episode_count','series_completion_status','series_author','series_author_raw','series_url','match_status','download_status','series_result_text'] if c in df.columns]
    rows=[]
    for _,g in df.groupby('_key',dropna=False):
        out={c:first_valid(g[c].tolist()) for c in reps}
        wd=ordered([norm_day(x) for x in g.get('weekday',pd.Series([],dtype=str)).tolist() if norm_day(x)])
        days=[]
        for x in g.get('serial_weekdays',pd.Series([],dtype=str)).tolist(): days += parse_days(x)
        days += wd
        serial=ordered(days)
        out['weekday_list']=','.join(wd); out['weekday_row_count']=len(g)
        out['serial_weekdays']=','.join(serial); out['serial_weekday_count']=len(serial)
        out['pre_collect_weekdays']=','.join([PREV[d] for d in serial if d in PREV])
        out['post_24h_collect_weekdays']=','.join(serial)
        out['is_download_collectable']=bool(str(out.get('series_url','')).strip() and str(out.get('series_product_no','')).strip())
        rows.append(out)
    return pd.DataFrame(rows)

def events(dedup_df):
    rows=[]
    d=dedup_df[dedup_df['series_url'].fillna('').astype(str).str.strip().ne('') & dedup_df['series_product_no'].fillna('').astype(str).str.strip().ne('')]
    for _,r in d.iterrows():
        ds=parse_days(r.get('serial_weekdays','')) or parse_days(r.get('weekday_list',''))
        for day in ds:
            out=r.to_dict(); out['release_weekday']=day; out['release_weekday_ko']=EN_TO_KO.get(day,day)
            out['pre_collect_weekday']=PREV.get(day,''); out['post_24h_collect_weekday']=day
            out['release_event_key']=f"{out.get('series_product_no','')}_{day}"; rows.append(out)
    ev=pd.DataFrame(rows)
    return ev.drop_duplicates(['series_product_no','release_weekday']) if not ev.empty else ev

def targets(ev, snap_day):
    pre=ev[ev['pre_collect_weekday']==snap_day].copy(); pre['collection_context']='pre_release'; pre['target_reason']='오늘 밤 10시 새 회차 공개 전 기준값 수집'
    post=ev[ev['post_24h_collect_weekday']==snap_day].copy(); post['collection_context']='post_24h'; post['target_reason']='어제 밤 10시 공개 이후 증가분 확인용 수집'
    return pd.concat([pre,post],ignore_index=True)

def make_driver():
    opt=webdriver.ChromeOptions(); opt.add_argument('--headless=new'); opt.add_argument('--window-size=1400,1000'); opt.add_argument('--disable-gpu'); opt.add_argument('--no-sandbox'); opt.add_argument('--disable-dev-shm-usage')
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opt)

def collect_one(driver,url):
    res={'series_download_text':'','series_download_count':None,'download_status':'unknown','current_url':''}
    if not url: res['download_status']='no_url'; return res
    try:
        driver.get(url); time.sleep(random.uniform(1.0,2.0)); res['current_url']=driver.current_url
        txt=''
        selectors=[(By.XPATH,'//*[@id="content"]/div[1]/div[2]/ul/li[2]/a/span'),(By.CSS_SELECTOR,'#content > div.end_head > div.user_action_area > ul > li:nth-child(2) > a > span'),(By.XPATH,'//*[@id="content"]//div[contains(@class, "user_action_area")]//ul/li[2]//span')]
        for by,sel in selectors:
            try:
                e=WebDriverWait(driver,4).until(EC.presence_of_element_located((by,sel))); txt=e.text.strip()
                if txt: break
            except Exception: pass
        if not txt:
            body=driver.find_element(By.TAG_NAME,'body').text if driver.find_elements(By.TAG_NAME,'body') else ''
            res['download_status']='login_required_or_adult' if ('로그인' in body and ('성인' in body or '19' in body or '본인 확인' in body)) else 'not_found'
            return res
        cnt=parse_download(txt); res['series_download_text']=txt; res['series_download_count']=cnt
        res['download_status']='ok' if cnt is not None else ('wrong_candidate_serial_link' if txt=='연재본 보기' else 'non_numeric_download_text')
        return res
    except Exception as e:
        res['download_status']=f'error: {e}'; return res

def collect_all(tdf,snap_day):
    if tdf.empty: print('No targets.'); return pd.DataFrame()
    sid=snapshot_id(); start=now_kst(); rows=[]; driver=make_driver()
    try:
        for i,r in tdf.iterrows():
            item_start=now_kst(); print(f"[{i+1}/{len(tdf)}] {r.get('collection_context')} {r.get('release_weekday')} {r.get('webtoon_title')}")
            info=collect_one(driver,r.get('series_url','')); item_end=now_kst()
            out=r.to_dict(); out.update(info); out['snapshot_id']=sid; out['snapshot_weekday']=snap_day; out['snapshot_weekday_ko']=EN_TO_KO.get(snap_day,snap_day)
            out['snapshot_started_at_kst']=start.strftime('%Y-%m-%d %H:%M:%S'); out['snapshot_date_kst']=start.strftime('%Y-%m-%d'); out['snapshot_time_kst']=start.strftime('%H:%M:%S')
            out['item_collect_started_at_kst']=item_start.strftime('%Y-%m-%d %H:%M:%S'); out['item_collect_finished_at_kst']=item_end.strftime('%Y-%m-%d %H:%M:%S'); out['item_collect_elapsed_seconds']=round((item_end-item_start).total_seconds(),2)
            out['collected_at_kst']=item_end.strftime('%Y-%m-%d %H:%M:%S'); out['collected_date_kst']=item_end.strftime('%Y-%m-%d'); out['collected_time_kst']=item_end.strftime('%H:%M:%S')
            rows.append(out); print(f" -> {info['series_download_text']} {info['series_download_count']} {info['download_status']}")
            time.sleep(random.uniform(0.8,1.8))
    finally:
        driver.quit()
    df=pd.DataFrame(rows); end=now_kst(); df['snapshot_finished_at_kst']=end.strftime('%Y-%m-%d %H:%M:%S'); df['snapshot_elapsed_seconds']=round((end-start).total_seconds(),2)
    return df

def append_history(snap):
    if snap.empty: return pd.DataFrame()
    hist=pd.concat([pd.read_csv(HISTORY_CSV),snap],ignore_index=True) if HISTORY_CSV.exists() else snap.copy()
    keys=[c for c in ['snapshot_id','collection_context','release_weekday','series_product_no','series_url'] if c in hist.columns]
    hist=hist.drop_duplicates(keys,keep='last'); hist.to_csv(HISTORY_CSV,index=False,encoding='utf-8-sig'); hist.to_excel(HISTORY_XLSX,index=False); return hist

REPORT_EXTRA_COLUMNS = [
    "baseline_found",
    "baseline_snapshot_id",
    "baseline_collected_at_kst",
    "baseline_download_text",
    "baseline_download_count",
    "download_increase",
    "download_increase_rate",
]


def norm_product_no(v):
    if v is None or pd.isna(v):
        return ""
    s = str(v).strip()
    s = re.sub(r"\.0$", "", s)
    return "" if s.lower() in ["nan", "none", "null"] else s


def report(snap, hist):
    """
    post_24h 수집분과 과거 pre_release 기준값을 매칭해 증가량을 계산합니다.

    v8 수정:
    - release_event_key 우선 매칭
    - 보조적으로 series_product_no + release_weekday 매칭
    - baseline 관련 컬럼을 항상 같은 스키마로 생성
    - Google Sheets increase_report 헤더 밀림 방지를 위해 컬럼 구조 고정
    """
    if snap is None or hist is None or snap.empty or hist.empty:
        return pd.DataFrame()

    post = snap[snap["collection_context"] == "post_24h"].copy()
    pre = hist[hist["collection_context"] == "pre_release"].copy()

    if post.empty:
        return pd.DataFrame()

    for c in REPORT_EXTRA_COLUMNS:
        if c not in post.columns:
            post[c] = None
    post["baseline_found"] = False

    if pre.empty:
        return post

    pre["dt"] = pd.to_datetime(pre["collected_at_kst"], errors="coerce")
    post["dt"] = pd.to_datetime(post["collected_at_kst"], errors="coerce")

    pre["_series_product_no_norm"] = pre["series_product_no"].apply(norm_product_no) if "series_product_no" in pre.columns else ""
    post["_series_product_no_norm"] = post["series_product_no"].apply(norm_product_no) if "series_product_no" in post.columns else ""

    rows = []

    for _, r in post.iterrows():
        out = r.to_dict()

        out.update({
            "baseline_found": False,
            "baseline_snapshot_id": "",
            "baseline_collected_at_kst": "",
            "baseline_download_text": "",
            "baseline_download_count": None,
            "download_increase": None,
            "download_increase_rate": None,
        })

        r_dt = r.get("dt")
        if pd.isna(r_dt):
            rows.append(out)
            continue

        cand = pd.DataFrame()

        # 1순위: release_event_key로 정확 매칭
        if "release_event_key" in pre.columns and "release_event_key" in post.columns:
            key = str(r.get("release_event_key", "")).strip()
            if key:
                cand = pre[
                    (pre["release_event_key"].astype(str).str.strip() == key)
                    & (pre["dt"] < r_dt)
                ]

        # 2순위: productNo + release_weekday 매칭
        if cand.empty:
            pno = r.get("_series_product_no_norm", "")
            day = str(r.get("release_weekday", "")).strip()
            if pno and day and "_series_product_no_norm" in pre.columns and "release_weekday" in pre.columns:
                cand = pre[
                    (pre["_series_product_no_norm"] == pno)
                    & (pre["release_weekday"].astype(str).str.strip() == day)
                    & (pre["dt"] < r_dt)
                ]

        if not cand.empty:
            b = cand.sort_values("dt").iloc[-1]
            cur = pd.to_numeric(r.get("series_download_count"), errors="coerce")
            base = pd.to_numeric(b.get("series_download_count"), errors="coerce")

            if pd.notna(cur) and pd.notna(base):
                inc = float(cur) - float(base)
                rate = inc / float(base) if float(base) != 0 else None
            else:
                inc = None
                rate = None

            out.update({
                "baseline_found": True,
                "baseline_snapshot_id": b.get("snapshot_id", ""),
                "baseline_collected_at_kst": b.get("collected_at_kst", ""),
                "baseline_download_text": b.get("series_download_text", ""),
                "baseline_download_count": base if pd.notna(base) else None,
                "download_increase": inc,
                "download_increase_rate": rate,
            })

        rows.append(out)

    result = pd.DataFrame(rows)

    # 내부 계산용 컬럼 제거
    drop_cols = [c for c in ["_series_product_no_norm"] if c in result.columns]
    if drop_cols:
        result = result.drop(columns=drop_cols)

    # 컬럼 순서 안정화: 원래 post 컬럼 + baseline 컬럼
    base_cols = [c for c in snap.columns if c in result.columns and c not in REPORT_EXTRA_COLUMNS]
    final_cols = base_cols + [c for c in REPORT_EXTRA_COLUMNS if c in result.columns]
    extra_cols = [c for c in result.columns if c not in final_cols and not c.startswith("_")]
    return result[final_cols + extra_cols]


def make_increase_event_key(df):
    """
    증가량 리포트 중복 제거용 이벤트 키를 만듭니다.

    같은 날 Daily를 수동으로 여러 번 실행하면 동일 작품/요일의 post_24h가
    여러 번 쌓일 수 있습니다. 운영 대시보드에서는 같은 공개 이벤트를
    한 번만 세야 하므로, 같은 post 수집일 + 같은 release_event_key는
    가장 늦게 수집된 행만 남깁니다.
    """
    out = pd.Series([""] * len(df), index=df.index, dtype="object")

    if "release_event_key" in df.columns:
        out = df["release_event_key"].fillna("").astype(str).str.strip()

    if "series_product_no" in df.columns:
        pno = df["series_product_no"].apply(norm_product_no)
    else:
        pno = pd.Series([""] * len(df), index=df.index, dtype="object")

    if "webtoon_title_id" in df.columns:
        tid = df["webtoon_title_id"].apply(norm_product_no)
    else:
        tid = pd.Series([""] * len(df), index=df.index, dtype="object")

    if "release_weekday" in df.columns:
        day = df["release_weekday"].fillna("").astype(str).str.strip()
    else:
        day = pd.Series([""] * len(df), index=df.index, dtype="object")

    fallback = pno + "_" + tid + "_" + day
    out = out.where(out.ne(""), fallback)
    return out


def build_full_increase_report(hist):
    """
    전체 snapshot_history에서 post_24h 증가량을 다시 계산합니다.

    v10 수정:
    - 같은 날 Daily를 여러 번 수동 실행해도 같은 작품/요일 공개 이벤트는
      최신 post_24h 1건만 increase_report에 남깁니다.
    - 그래서 대시보드에서 주간 증가량이 중복 합산되는 문제를 방지합니다.
    """
    if hist is None or hist.empty:
        return pd.DataFrame()

    post_all = hist[hist["collection_context"] == "post_24h"].copy()
    if post_all.empty:
        return pd.DataFrame()

    full = report(post_all, hist)

    if full.empty:
        return full

    full["_dt_for_dedupe"] = pd.to_datetime(full.get("collected_at_kst"), errors="coerce")
    if "collected_date_kst" in full.columns:
        full["_post_collect_date"] = full["collected_date_kst"].fillna("").astype(str).str.strip()
    else:
        full["_post_collect_date"] = full["_dt_for_dedupe"].dt.strftime("%Y-%m-%d")

    full["_increase_event_key"] = make_increase_event_key(full)

    full = (
        full.sort_values(["_post_collect_date", "_increase_event_key", "_dt_for_dedupe"])
        .drop_duplicates(["_post_collect_date", "_increase_event_key"], keep="last")
        .drop(columns=["_dt_for_dedupe", "_post_collect_date", "_increase_event_key"], errors="ignore")
    )

    return full

def main():
    print('KST now:',now_kst().strftime('%Y-%m-%d %H:%M:%S'))
    snap_day=today_weekday(); print('snapshot weekday:',snap_day,EN_TO_KO.get(snap_day,''))
    raw=load_mapping(); dd=dedup(raw); dd.to_csv(DEDUP_CSV,index=False,encoding='utf-8-sig'); dd.to_excel(DEDUP_XLSX,index=False)
    ev=events(dd); tdf=targets(ev,snap_day); print('dedup rows:',len(dd),'event rows:',len(ev),'target rows:',len(tdf));
    if not tdf.empty: print(tdf['collection_context'].value_counts())
    snap=collect_all(tdf,snap_day)
    if not snap.empty:
        sid=snap['snapshot_id'].iloc[0]; snap_csv=DATA/f'series_download_snapshot_two_stage_{sid}.csv'; snap_xlsx=DATA/f'series_download_snapshot_two_stage_{sid}.xlsx'; snap.to_csv(snap_csv,index=False,encoding='utf-8-sig'); snap.to_excel(snap_xlsx,index=False); print('saved',snap_csv)
    hist=append_history(snap)
    rep=report(snap,hist)
    full_rep=build_full_increase_report(hist)

    if not rep.empty:
        sid=snap['snapshot_id'].iloc[0] if not snap.empty else snapshot_id(); rcsv=DATA/f'series_download_increase_report_{sid}.csv'; rxlsx=DATA/f'series_download_increase_report_{sid}.xlsx'; rep.to_csv(rcsv,index=False,encoding='utf-8-sig'); rep.to_excel(rxlsx,index=False); print('saved',rcsv)

    if not full_rep.empty:
        full_rep.to_csv(INCREASE_HISTORY_CSV,index=False,encoding='utf-8-sig'); full_rep.to_excel(INCREASE_HISTORY_XLSX,index=False); print('saved',INCREASE_HISTORY_CSV)

    # Google Sheets sync
    # snapshot_history / problem_rows는 이번 실행분만 append합니다.
    # increase_report는 헤더 밀림을 막기 위해 전체 재계산본으로 교체합니다.
    # dedup_mapping은 중복 적재를 막기 위해 매일 최신 상태로 전체 교체합니다.
    sync_google_sheets(snap, rep, dd, full_rep)

if __name__=='__main__': main()
