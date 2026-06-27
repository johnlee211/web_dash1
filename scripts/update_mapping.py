import os
import re
import json
import time
import random
import shutil
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qs

import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


# =========================
# 0. Settings
# =========================

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

KST = ZoneInfo("Asia/Seoul")
HEADLESS = True
WAIT_SEC = 10
MAX_SERIES_CANDIDATES = int(os.getenv("MAX_SERIES_CANDIDATES", "8"))
FORCE_RECHECK_ALL = os.getenv("FORCE_RECHECK_ALL", "0").strip() == "1"

WEBTOON_HOME_URL = "https://comic.naver.com/webtoon"
SERIES_HOME_URL = "https://series.naver.com/comic/home.series?isWebtoonAgreePopUp=true"

MAPPING_CSV = DATA / "naver_webtoon_series_productNo_mapping_v2_title_cleaned.csv"
MAPPING_XLSX = DATA / "naver_webtoon_series_productNo_mapping_v2_title_cleaned.xlsx"

PREVIOUS_MAPPING_CSV = DATA / "naver_webtoon_series_productNo_mapping_previous.csv"
PREVIOUS_MAPPING_XLSX = DATA / "naver_webtoon_series_productNo_mapping_previous.xlsx"

ADULT_CSV = DATA / "adult_login_required_webtoons_v2.csv"
ADULT_XLSX = DATA / "adult_login_required_webtoons_v2.xlsx"

RAW_CSV = DATA / "naver_webtoon_weekly_list_raw.csv"
RAW_XLSX = DATA / "naver_webtoon_weekly_list_raw.xlsx"

DUPLICATE_CSV = DATA / "naver_webtoon_series_mapping_duplicate_rows.csv"
DUPLICATE_XLSX = DATA / "naver_webtoon_series_mapping_duplicate_rows.xlsx"

RECHECK_CSV = DATA / "weekly_mapping_recheck_targets.csv"
RECHECK_XLSX = DATA / "weekly_mapping_recheck_targets.xlsx"

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_MAP = {
    1: "mon",
    2: "tue",
    3: "wed",
    4: "thu",
    5: "fri",
    6: "sat",
    7: "sun",
}
WEEKDAY_KO = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}

TITLE_BADGES = {
    "신작", "청유물", "청불", "휴재", "완결", "UP", "NEW", "무료", "독점",
    "매일+", "새로운", "업데이트",
}

SERIES_COLS = [
    "series_search_rank",
    "series_title",
    "series_title_raw",
    "series_episode_info",
    "series_total_episode_count",
    "series_completion_status",
    "series_author",
    "series_author_raw",
    "series_url",
    "series_product_no",
    "series_result_text",
    "is_compilation",
    "has_total_episode_info",
    "match_score",
    "match_reason",
    "match_status",
    "selected_rule",
    "checked_candidate_count",
    "series_download_text",
    "series_download_count",
    "download_status",
]


# =========================
# 1. Utils
# =========================

def now_kst():
    return datetime.now(KST)


def sleep_random(a=0.6, b=1.4):
    time.sleep(random.uniform(a, b))


def safe_text(x):
    if x is None:
        return ""
    s = str(x).strip()
    if s.lower() in ["nan", "none", "null"]:
        return ""
    return s


def normalize_text(text):
    text = safe_text(text).lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^\w가-힣]", "", text)
    return text


def norm_id(x):
    s = safe_text(x)
    s = re.sub(r"\.0$", "", s)
    return s


def extract_title_id(url):
    try:
        parsed = urlparse(safe_text(url))
        qs = parse_qs(parsed.query)
        return qs.get("titleId", [""])[0]
    except Exception:
        return ""


def extract_product_no(url):
    m = re.search(r"productNo=(\d+)", safe_text(url))
    return m.group(1) if m else ""


def series_url_from_product_no(product_no):
    product_no = norm_id(product_no)
    if not product_no:
        return ""
    return f"https://series.naver.com/comic/detail.series?productNo={product_no}"


def parse_number(text):
    raw = safe_text(text)
    raw = raw.replace(",", "").replace("다운로드", "").replace("회", "").strip()

    if not raw or raw in ["연재본 보기", "보기", "소장", "대여", "무료"]:
        return None

    for unit, mul in [("억", 100000000), ("만", 10000), ("천", 1000)]:
        m = re.search(r"([\d.]+)\s*" + unit, raw)
        if m:
            return int(float(m.group(1)) * mul)

    m = re.search(r"\d+", raw)
    return int(m.group()) if m else None


def clean_author_text(text):
    text = safe_text(text)
    if not text:
        return ""

    text = text.replace("글", "")
    text = text.replace("그림", "")
    text = text.replace("원작", "")
    text = text.replace("작가", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("/,|")
    return text


def extract_author_from_body_text(body_text):
    if not body_text:
        return ""

    lines = [x.strip() for x in body_text.splitlines() if x.strip()]

    for i, line in enumerate(lines):
        if line in ["글", "그림", "원작", "작가"]:
            if i + 1 < len(lines):
                return lines[i + 1]

    joined = " ".join(lines[:120])

    patterns = [
        r"글\s*([가-힣A-Za-z0-9_.,\s]+?)\s*(그림|원작|연재|장르)",
        r"작가\s*([가-힣A-Za-z0-9_.,\s]+?)\s*(연재|장르|소개)",
    ]

    for pat in patterns:
        m = re.search(pat, joined)
        if m:
            return m.group(1).strip()

    return ""


def parse_serial_weekdays_from_text(text):
    text = safe_text(text)

    if not text:
        return ""

    if "매일" in text:
        return ",".join(WEEKDAYS)

    days = []

    for en, ko in WEEKDAY_KO.items():
        if ko in text:
            days.append(en)

    return ",".join([d for d in WEEKDAYS if d in days])


def has_serial_episode_info(text):
    return bool(re.search(r"총\s*\d+\s*화", safe_text(text)))


def is_compilation_result(text):
    return "합본" in safe_text(text)


def is_wrong_download_text(text):
    return safe_text(text) in ["연재본 보기"]


def is_bad_title(title):
    t = safe_text(title)
    if not t:
        return True
    if t in TITLE_BADGES:
        return True
    if len(t) <= 1:
        return True
    return False


def parse_series_title_info(title):
    raw = safe_text(title)

    if not raw:
        return {
            "series_title_clean": "",
            "series_episode_info": "",
            "series_total_episode_count": None,
            "series_completion_status": "",
        }

    episode_info = ""
    total_episode_count = None
    completion_status = ""

    info_pattern = r"\(\s*(총\s*\d+\s*화\s*/\s*(?:미완결|완결))\s*\)"
    m = re.search(info_pattern, raw)

    if m:
        episode_info = m.group(1).strip()
        clean_title = re.sub(info_pattern, "", raw).strip()
    else:
        info_pattern_no_paren = r"총\s*\d+\s*화\s*/\s*(?:미완결|완결)"
        m = re.search(info_pattern_no_paren, raw)
        if m:
            episode_info = m.group(0).strip()
            clean_title = re.sub(info_pattern_no_paren, "", raw).strip()
        else:
            clean_title = raw

    if episode_info:
        count_match = re.search(r"총\s*(\d+)\s*화", episode_info)
        if count_match:
            total_episode_count = int(count_match.group(1))

        if "미완결" in episode_info:
            completion_status = "미완결"
        elif "완결" in episode_info:
            completion_status = "완결"

    clean_title = re.sub(r"\s+", " ", clean_title).strip()

    return {
        "series_title_clean": clean_title,
        "series_episode_info": episode_info,
        "series_total_episode_count": total_episode_count,
        "series_completion_status": completion_status,
    }


def clean_series_title_columns(df):
    df = df.copy()

    if "series_title" not in df.columns:
        return df

    if "series_title_raw" not in df.columns:
        df["series_title_raw"] = df["series_title"]

    parsed = df["series_title_raw"].apply(parse_series_title_info).apply(pd.Series)

    df["series_title"] = parsed["series_title_clean"]
    df["series_episode_info"] = parsed["series_episode_info"]
    df["series_total_episode_count"] = parsed["series_total_episode_count"]
    df["series_completion_status"] = parsed["series_completion_status"]

    return df


def is_login_or_adult_page(driver):
    """
    일반 페이지의 '로그인' 문구만 보고 성인/로그인 필요로 보지 않습니다.
    URL이 실제 로그인/인증 페이지로 이동했거나, 본문에 강한 성인 인증 문구가 있을 때만 True.
    """
    current_url = driver.current_url.lower()

    url_patterns = [
        "nid.naver.com",
        "nidlogin.login",
        "adult",
        "age",
        "auth",
    ]

    if any(p in current_url for p in url_patterns):
        return True

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        body_text = ""

    strong_patterns = [
        "성인 인증",
        "연령 확인",
        "19세",
        "19금",
        "본인 확인",
        "청소년 이용불가",
    ]

    if any(p in body_text for p in strong_patterns):
        return True

    return False


# =========================
# 2. Google Sheets
# =========================

def get_sheets_service():
    if not GOOGLE_SHEETS_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        print("Google Sheets settings are missing. Skip Google Sheets sync.")
        return None

    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=credentials)


def get_sheet_names(service, spreadsheet_id):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def ensure_sheet_exists(service, spreadsheet_id, sheet_name):
    if service is None:
        return

    if sheet_name in get_sheet_names(service, spreadsheet_id):
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
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
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy().fillna("")

    for col in out.columns:
        out[col] = out[col].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S") if hasattr(x, "strftime") else x)

    return out.astype(str)


def replace_df_to_sheet(service, spreadsheet_id, sheet_name, df):
    if service is None:
        return

    if df is None or df.empty:
        print(f"No data to replace: {sheet_name}")
        return

    df2 = prepare_df_for_sheet(df)
    values = [list(df2.columns)] + df2.values.tolist()

    clear_sheet(service, spreadsheet_id, sheet_name)

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    print(f"Replaced Google Sheet tab: {sheet_name} ({len(df2)} rows)")


def append_log_to_sheet(service, spreadsheet_id, row):
    """
    Google Sheets 로그 탭을 갱신합니다.

    v6 정책:
    - mapping_update_log:
      매 실행마다 무조건 탭 내용을 비우고,
      현재 코드의 최신 헤더 + 이번 실행 1행만 남깁니다.
      그래서 구버전 헤더 때문에 값이 밀리는 문제가 발생하지 않습니다.

    - mapping_update_log_history:
      같은 헤더로 누적 append합니다.
      단, history 탭의 헤더가 현재 헤더와 다르면 이 탭도 초기화합니다.
    """
    if service is None:
        return

    header = list(row.keys())
    current_row = [[safe_text(row.get(c, "")) for c in header]]

    # 1) 최신 로그 탭: 무조건 초기화 후 이번 실행 1행만 기록
    latest_sheet = "mapping_update_log"
    ensure_sheet_exists(service, spreadsheet_id, latest_sheet)

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{latest_sheet}!A:ZZ",
        body={},
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{latest_sheet}!A1",
        valueInputOption="RAW",
        body={"values": [header] + current_row},
    ).execute()

    print("[mapping_update_log] Reset and wrote latest mapping update log.")

    # 2) 누적 히스토리 탭: 헤더가 같으면 append, 다르면 초기화 후 시작
    history_sheet = "mapping_update_log_history"
    ensure_sheet_exists(service, spreadsheet_id, history_sheet)

    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{history_sheet}!A1:ZZ1",
    ).execute()

    values = result.get("values", [])
    existing_header = values[0] if values else []

    if existing_header != header:
        if existing_header:
            print("[mapping_update_log_history] Header changed. Clear history tab and recreate header.")
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{history_sheet}!A:ZZ",
            body={},
        ).execute()

        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{history_sheet}!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{history_sheet}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": current_row},
    ).execute()

    print("[mapping_update_log_history] Appended mapping update log.")



# =========================
# 3. Selenium
# =========================

def make_driver(headless=True):
    options = webdriver.ChromeOptions()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(35)

    return driver


def safe_find_text(driver, selectors, by=By.CSS_SELECTOR):
    for selector in selectors:
        try:
            elem = driver.find_element(by, selector)
            txt = elem.text.strip()
            if txt:
                return txt
        except Exception:
            pass
    return ""


# =========================
# 4. Webtoon list / author
# =========================

def collect_webtoon_weekly_list(driver):
    driver.get(WEBTOON_HOME_URL)

    WebDriverWait(driver, WAIT_SEC).until(
        EC.presence_of_element_located((By.XPATH, '//*[@id="container"]/div[3]/div[2]'))
    )

    rows = []

    for day_idx, weekday in WEEKDAY_MAP.items():
        day_xpath = f'//*[@id="container"]/div[3]/div[2]/div[{day_idx}]'

        try:
            day_box = driver.find_element(By.XPATH, day_xpath)
        except NoSuchElementException:
            print(f"[경고] {weekday} 영역을 찾지 못했습니다.")
            continue

        items = day_box.find_elements(By.XPATH, ".//ul/li")
        print(f"[수집] {weekday}: {len(items)}개 작품 발견")

        for rank, item in enumerate(items, start=1):
            title = ""
            href = ""

            # 검증된 방식: li 내부의 제목 span만 우선 사용
            title_xpaths = [
                ".//div/a/span/span",
                ".//a[contains(@href, 'titleId=')]//span/span",
                ".//a[contains(@href, 'titleId=')]",
            ]

            for xp in title_xpaths:
                try:
                    elem = item.find_element(By.XPATH, xp)
                    cand = elem.text.strip().split("\n")[0].strip()
                    if not is_bad_title(cand):
                        title = cand
                        break
                except Exception:
                    pass

            try:
                a = item.find_element(By.XPATH, ".//a[contains(@href, 'titleId=')]")
                href = a.get_attribute("href")
            except Exception:
                try:
                    a = item.find_element(By.XPATH, ".//a")
                    href = a.get_attribute("href")
                except Exception:
                    pass

            title_id = extract_title_id(href)

            if title and title_id:
                rows.append({
                    "weekday": weekday,
                    "weekday_ko": WEEKDAY_KO.get(weekday, weekday),
                    "rank": rank,
                    "webtoon_title": title,
                    "webtoon_url": href,
                    "webtoon_title_id": title_id,
                    "collected_at_kst": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                })

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["weekday", "webtoon_title_id"], keep="first").reset_index(drop=True)
    return df


def collect_webtoon_author_and_serial(driver, webtoon_url):
    result = {
        "webtoon_author": "",
        "adult_or_login_required": False,
        "author_collect_status": "unknown",
        "serial_weekday_text": "",
        "serial_weekdays": "",
    }

    if not webtoon_url:
        result["author_collect_status"] = "no_url"
        return result

    try:
        driver.get(webtoon_url)
        sleep_random()

        if is_login_or_adult_page(driver):
            result["adult_or_login_required"] = True
            result["author_collect_status"] = "login_or_adult_required"
            return result

        author_selectors = [
            "span.ContentMetaInfo__author--CTAAP",
            "a.ContentMetaInfo__link--xTtO6",
            ".ContentMetaInfo__info_item--utGrf a",
            ".EpisodeListInfo__author--d9PjH",
            ".comicinfo .detail h2 span",
        ]

        author = safe_find_text(driver, author_selectors)

        body_text = ""
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            body_text = ""

        if not author:
            author = extract_author_from_body_text(body_text)

        if author:
            result["webtoon_author"] = clean_author_text(author)
            result["author_collect_status"] = "ok"
        else:
            result["webtoon_author"] = ""
            result["author_collect_status"] = "not_found"

        # 연재요일 텍스트
        serial_patterns = [
            r"([월화수목금토일,\s]+|매일\+?)\s*연재",
            r"매주\s*([월화수목금토일,\s]+)",
        ]

        for pat in serial_patterns:
            m = re.search(pat, body_text)
            if m:
                result["serial_weekday_text"] = m.group(0).strip()
                result["serial_weekdays"] = parse_serial_weekdays_from_text(result["serial_weekday_text"])
                break

        return result

    except Exception as e:
        result["webtoon_author"] = ""
        result["author_collect_status"] = f"error: {e}"
        return result


def add_authors_and_serial_to_webtoon_df(driver, df):
    rows = []

    for i, row in df.iterrows():
        print(f"[작가 확인] {i + 1}/{len(df)} {row['weekday']} {row['webtoon_title']}")

        info = collect_webtoon_author_and_serial(driver, row["webtoon_url"])
        merged = row.to_dict()
        merged.update(info)
        rows.append(merged)

        sleep_random(0.4, 1.0)

    return pd.DataFrame(rows)


def fill_weekday_lists(df):
    df = df.copy()

    if df.empty:
        return df

    by_title_id = (
        df.groupby("webtoon_title_id")["weekday"]
        .apply(lambda s: ",".join([d for d in WEEKDAYS if d in set(s.astype(str))]))
        .to_dict()
    )

    df["weekday_list"] = df["webtoon_title_id"].astype(str).map(by_title_id)

    def fill_serial(row):
        serial = safe_text(row.get("serial_weekdays", ""))
        if serial:
            return serial
        return safe_text(row.get("weekday_list", row.get("weekday", "")))

    df["serial_weekdays"] = df.apply(fill_serial, axis=1)

    return df


# =========================
# 5. Existing mapping reuse
# =========================

def load_existing_mapping():
    if MAPPING_CSV.exists():
        try:
            df = pd.read_csv(MAPPING_CSV)
            print(f"[기존 매핑 로드] {MAPPING_CSV} / {len(df)} rows")
            return df
        except Exception as e:
            print(f"[경고] 기존 CSV 로드 실패: {e}")

    if MAPPING_XLSX.exists():
        try:
            df = pd.read_excel(MAPPING_XLSX)
            print(f"[기존 매핑 로드] {MAPPING_XLSX} / {len(df)} rows")
            return df
        except Exception as e:
            print(f"[경고] 기존 XLSX 로드 실패: {e}")

    print("[기존 매핑 없음] 전체 작품을 신규 검색합니다.")
    return pd.DataFrame()


def backup_existing_mapping():
    if MAPPING_CSV.exists():
        shutil.copy(MAPPING_CSV, PREVIOUS_MAPPING_CSV)
    if MAPPING_XLSX.exists():
        shutil.copy(MAPPING_XLSX, PREVIOUS_MAPPING_XLSX)


def normalize_existing_mapping(df):
    df = df.copy()

    for c in ["webtoon_title_id", "series_product_no", "series_url"]:
        if c not in df.columns:
            df[c] = ""

    df["webtoon_title_id"] = df["webtoon_title_id"].apply(norm_id)
    df["series_product_no"] = df["series_product_no"].apply(norm_id)

    empty_url = df["series_url"].fillna("").astype(str).str.strip().eq("") & df["series_product_no"].ne("")
    df.loc[empty_url, "series_url"] = df.loc[empty_url, "series_product_no"].apply(series_url_from_product_no)

    return df


def choose_existing_row(group):
    g = group.copy()

    if "series_product_no" in g.columns:
        has_pno = g["series_product_no"].fillna("").astype(str).str.strip().ne("")
        g2 = g[has_pno]
        if not g2.empty:
            if "download_status" in g2.columns:
                ok = g2[g2["download_status"].astype(str).eq("ok")]
                if not ok.empty:
                    return ok.iloc[0]
            if "match_status" in g2.columns:
                matched = g2[g2["match_status"].astype(str).eq("matched")]
                if not matched.empty:
                    return matched.iloc[0]
            return g2.iloc[0]

    return g.iloc[0]


def build_existing_lookup(existing_df):
    if existing_df.empty or "webtoon_title_id" not in existing_df.columns:
        return {}

    existing_df = normalize_existing_mapping(existing_df)
    lookup = {}

    for tid, group in existing_df.groupby("webtoon_title_id", dropna=False):
        tid = norm_id(tid)
        if not tid:
            continue
        lookup[tid] = choose_existing_row(group).to_dict()

    return lookup


def row_has_reusable_product(row):
    if FORCE_RECHECK_ALL:
        return False

    pno = norm_id(row.get("series_product_no", ""))
    url = safe_text(row.get("series_url", ""))

    if pno and not url:
        return True

    if pno and url:
        return True

    return False


def merge_current_with_existing(current_df, existing_lookup):
    rows = []
    recheck_rows = []

    for _, row in current_df.iterrows():
        cur = row.to_dict()
        tid = norm_id(cur.get("webtoon_title_id", ""))
        old = existing_lookup.get(tid, {})

        out = cur.copy()

        if old and row_has_reusable_product(old):
            # 현재 웹툰 목록 정보는 최신값으로 유지하고, 시리즈 매핑 정보만 기존 것을 재사용
            for c in SERIES_COLS:
                if c in old:
                    out[c] = old.get(c, "")

            out["mapping_source"] = "reused_existing_product_no"
            out["needs_series_recheck"] = False
        else:
            for c in SERIES_COLS:
                out[c] = old.get(c, "") if old else ""

            out["mapping_source"] = "needs_series_search"
            out["needs_series_recheck"] = True
            recheck_rows.append(out.copy())

        rows.append(out)

    merged_df = pd.DataFrame(rows)
    recheck_df = pd.DataFrame(recheck_rows)

    return merged_df, recheck_df


# =========================
# 6. Series search
# =========================

def extract_candidate_title_from_item(item, fallback_text):
    title = ""

    title_xpaths = [
        ".//a[contains(@href, 'productNo=')]",
        ".//div/a",
        ".//a",
    ]

    for xp in title_xpaths:
        try:
            elem = item.find_element(By.XPATH, xp)
            title = elem.text.strip()
            if title:
                break
        except Exception:
            pass

    if not title:
        lines = [x.strip() for x in fallback_text.splitlines() if x.strip()]
        if lines:
            title = lines[0]

    return title


def extract_candidate_author_from_item(item, fallback_text):
    author = ""

    author_xpaths = [
        ".//div/p[1]",
        "./div/p[1]",
    ]

    for xp in author_xpaths:
        try:
            elem = item.find_element(By.XPATH, xp)
            author = elem.text.strip()
            if author:
                break
        except Exception:
            pass

    if author:
        if normalize_text(author) in ["평점"]:
            author = ""
        elif "평점" in author and len(author) <= 6:
            author = ""

    if not author:
        lines = [x.strip() for x in fallback_text.splitlines() if x.strip()]
        joined = " ".join(lines)

        patterns = [
            r"글\s*([가-힣A-Za-z0-9_.,\s]+?)(그림|원작|평점|총|$)",
            r"그림\s*([가-힣A-Za-z0-9_.,\s]+?)(원작|평점|총|$)",
            r"작가\s*([가-힣A-Za-z0-9_.,\s]+?)(평점|총|$)",
        ]

        found = []

        for p in patterns:
            m = re.search(p, joined)
            if m:
                found.append(m.group(1).strip())

        if found:
            author = " / ".join(found)

    return clean_author_text(author)


def search_series_by_title(driver, title):
    driver.get(SERIES_HOME_URL)

    WebDriverWait(driver, WAIT_SEC).until(
        EC.presence_of_element_located((By.XPATH, '//*[@id="ac_input1"]'))
    )

    search_box = driver.find_element(By.XPATH, '//*[@id="ac_input1"]')
    search_box.clear()
    search_box.send_keys(title)
    search_box.send_keys(Keys.ENTER)

    sleep_random(1.0, 2.0)

    try:
        WebDriverWait(driver, WAIT_SEC).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="content"]'))
        )
    except TimeoutException:
        pass

    candidates = []

    possible_result_xpaths = [
        '//*[@id="content"]/div[2]/div[2]/ul/li',
        '//*[@id="content"]//ul/li',
    ]

    result_items = []

    for xp in possible_result_xpaths:
        try:
            result_items = driver.find_elements(By.XPATH, xp)
            if result_items:
                break
        except Exception:
            pass

    for rank, item in enumerate(result_items[:MAX_SERIES_CANDIDATES], start=1):
        text = item.text.strip()

        if not text:
            continue

        href = ""

        try:
            a = item.find_element(By.XPATH, ".//a[contains(@href, 'productNo=')]")
            href = a.get_attribute("href")
        except Exception:
            try:
                a = item.find_element(By.XPATH, ".//a")
                href = a.get_attribute("href")
            except Exception:
                pass

        product_no = extract_product_no(href)

        candidate_title = extract_candidate_title_from_item(item, text)
        candidate_author = extract_candidate_author_from_item(item, text)

        candidate = {
            "series_search_rank": rank,
            "series_title": candidate_title,
            "series_title_raw": candidate_title,
            "series_author": candidate_author,
            "series_author_raw": candidate_author,
            "series_url": href,
            "series_product_no": product_no,
            "series_result_text": text,
            "is_compilation": is_compilation_result(candidate_title) or is_compilation_result(text),
            "has_total_episode_info": has_serial_episode_info(text),
        }

        if product_no:
            candidates.append(candidate)

    return candidates


def score_series_candidate(webtoon_title, webtoon_author, candidate):
    wt = normalize_text(webtoon_title)
    wa = normalize_text(webtoon_author)

    ct = normalize_text(candidate.get("series_title", ""))
    ca = normalize_text(candidate.get("series_author", ""))
    raw = normalize_text(candidate.get("series_result_text", ""))

    score = 0
    reasons = []

    if wt and ct and wt == ct:
        score += 100
        reasons.append("title_exact")
    elif wt and ct and (wt in ct or ct in wt):
        score += 70
        reasons.append("title_contains")
    elif wt and wt in raw:
        score += 50
        reasons.append("title_in_result_text")

    if wa:
        if wa in ca:
            score += 40
            reasons.append("author_exact_or_contains")
        elif wa in raw:
            score += 30
            reasons.append("author_in_result_text")
        else:
            author_tokens = re.split(r"[/,| ]+", webtoon_author)
            author_tokens = [normalize_text(x) for x in author_tokens if normalize_text(x)]

            for token in author_tokens:
                if token and token in raw:
                    score += 15
                    reasons.append("author_token_match")
                    break

    if candidate.get("is_compilation", False):
        score -= 120
        reasons.append("penalty_compilation")

    if candidate.get("has_total_episode_info", False):
        score += 25
        reasons.append("bonus_total_episode_info")

    rank = candidate.get("series_search_rank", 999)

    if rank == 1:
        score += 10
        reasons.append("bonus_rank_1")
    elif rank == 2:
        score += 5
        reasons.append("bonus_rank_2")

    return score, ",".join(reasons)


def rank_series_candidates(webtoon_title, webtoon_author, candidates):
    scored = []

    for cand in candidates:
        score, reason = score_series_candidate(webtoon_title, webtoon_author, cand)
        cand2 = cand.copy()
        cand2["match_score"] = score
        cand2["match_reason"] = reason
        scored.append(cand2)

    return sorted(scored, key=lambda x: x["match_score"], reverse=True)


def collect_series_download_count(driver, series_url):
    if not series_url:
        return {
            "series_download_text": "",
            "series_download_count": None,
            "download_status": "no_url",
        }

    try:
        driver.get(series_url)
        sleep_random(1.0, 2.0)

        download_text = ""

        selectors = [
            (By.XPATH, '//*[@id="content"]/div[1]/div[2]/ul/li[2]/a/span'),
            (By.CSS_SELECTOR, "#content > div.end_head > div.user_action_area > ul > li:nth-child(2) > a > span"),
            (By.XPATH, '//*[@id="content"]//div[contains(@class, "user_action_area")]//ul/li[2]//span'),
        ]

        for by, selector in selectors:
            try:
                elem = WebDriverWait(driver, 4).until(
                    EC.presence_of_element_located((by, selector))
                )
                download_text = elem.text.strip()
                if download_text:
                    break
            except Exception:
                pass

        if not download_text:
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                body_text = ""

            if "로그인" in body_text and ("성인" in body_text or "19" in body_text or "본인 확인" in body_text):
                return {
                    "series_download_text": "",
                    "series_download_count": None,
                    "download_status": "login_required_or_adult",
                }

            return {
                "series_download_text": "",
                "series_download_count": None,
                "download_status": "not_found",
            }

        download_count = parse_number(download_text)

        if is_wrong_download_text(download_text):
            return {
                "series_download_text": download_text,
                "series_download_count": None,
                "download_status": "wrong_candidate_serial_link",
            }

        if download_count is None:
            return {
                "series_download_text": download_text,
                "series_download_count": None,
                "download_status": "non_numeric_download_text",
            }

        return {
            "series_download_text": download_text,
            "series_download_count": download_count,
            "download_status": "ok",
        }

    except Exception as e:
        return {
            "series_download_text": "",
            "series_download_count": None,
            "download_status": f"error: {e}",
        }


def choose_best_candidate_with_download_check(driver, webtoon_title, webtoon_author, candidates):
    if not candidates:
        return None

    ranked_candidates = rank_series_candidates(webtoon_title, webtoon_author, candidates)
    checked_candidates = []

    for cand in ranked_candidates:
        download_info = collect_series_download_count(driver, cand.get("series_url", ""))

        cand_checked = cand.copy()
        cand_checked.update(download_info)
        checked_candidates.append(cand_checked)

        if cand_checked.get("is_compilation", False):
            continue

        if cand_checked.get("download_status") == "ok" and cand_checked.get("series_download_count") is not None:
            cand_checked["match_status"] = "matched"
            cand_checked["selected_rule"] = "first_non_compilation_numeric_download"
            cand_checked["checked_candidate_count"] = len(checked_candidates)
            return cand_checked

        if cand_checked.get("download_status") in [
            "wrong_candidate_serial_link",
            "non_numeric_download_text",
            "not_found",
        ]:
            continue

    non_compilation = [c for c in checked_candidates if not c.get("is_compilation", False)]

    if non_compilation:
        best = non_compilation[0]
        best["match_status"] = "review_needed"
        best["selected_rule"] = "non_compilation_but_download_not_ok"
        best["checked_candidate_count"] = len(checked_candidates)
        return best

    best = checked_candidates[0]
    best["match_status"] = "review_needed"
    best["selected_rule"] = "fallback_first_checked_candidate"
    best["checked_candidate_count"] = len(checked_candidates)
    return best


def search_missing_series(driver, merged_df):
    df = merged_df.copy()

    if "needs_series_recheck" not in df.columns:
        df["needs_series_recheck"] = True

    target_idx = df.index[df["needs_series_recheck"] == True].tolist()
    target_df = df.loc[target_idx].copy()

    target_df.to_csv(RECHECK_CSV, index=False, encoding="utf-8-sig")
    target_df.to_excel(RECHECK_XLSX, index=False)

    print(f"[3단계] 네이버 시리즈 검색 및 productNo 매칭 시작")
    print(f"[3단계 대상] 전체 {len(df)}개 중 신규/미매칭/재검색 대상 {len(target_idx)}개")

    for n, idx in enumerate(target_idx, start=1):
        row = df.loc[idx].to_dict()
        title = row.get("webtoon_title", "")
        author = row.get("webtoon_author", "")

        print(f"[시리즈 검색] {n}/{len(target_idx)} idx={idx + 1} {title} / 작가: {author}")

        try:
            candidates = search_series_by_title(driver, title)
            best = choose_best_candidate_with_download_check(driver, title, author, candidates)

            if best is None:
                best = {
                    "series_search_rank": None,
                    "series_title": "",
                    "series_title_raw": "",
                    "series_author": "",
                    "series_author_raw": "",
                    "series_url": "",
                    "series_product_no": "",
                    "series_result_text": "",
                    "is_compilation": False,
                    "has_total_episode_info": False,
                    "match_score": 0,
                    "match_reason": "",
                    "match_status": "no_result",
                    "selected_rule": "no_result",
                    "checked_candidate_count": 0,
                    "series_download_text": "",
                    "series_download_count": None,
                    "download_status": "no_result",
                }

            for c in SERIES_COLS:
                df.loc[idx, c] = best.get(c, "")

            df.loc[idx, "mapping_source"] = "searched_series"
            df.loc[idx, "needs_series_recheck"] = False

            print(
                f"  → productNo={best.get('series_product_no', '')} / "
                f"title={best.get('series_title', '')} / "
                f"download={best.get('series_download_text', '')} / "
                f"status={best.get('match_status', '')}"
            )

        except Exception as e:
            print(f"[실패] {title}: {e}")

            for c in SERIES_COLS:
                df.loc[idx, c] = ""

            df.loc[idx, "match_status"] = f"error: {e}"
            df.loc[idx, "download_status"] = f"error: {e}"
            df.loc[idx, "mapping_source"] = "search_error"

        sleep_random(0.8, 1.8)

    return df


# =========================
# 7. Main
# =========================

def main():
    started_at = now_kst()

    print(f"KST now: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print("[주간 매핑 업데이트] 시작")
    print(f"FORCE_RECHECK_ALL={FORCE_RECHECK_ALL}")

    existing_df = load_existing_mapping()
    existing_lookup = build_existing_lookup(existing_df)

    if not existing_df.empty:
        backup_existing_mapping()

    driver = make_driver(headless=HEADLESS)

    try:
        print("[1단계] 네이버웹툰 요일별 목록 수집 시작")
        webtoon_df = collect_webtoon_weekly_list(driver)
        print(f"[1단계 완료] 총 {len(webtoon_df)}개 작품 수집")

        print("[2단계] 작품별 작가명/연재요일 수집 시작")
        webtoon_df = add_authors_and_serial_to_webtoon_df(driver, webtoon_df)
        webtoon_df = fill_weekday_lists(webtoon_df)

        webtoon_df.to_csv(RAW_CSV, index=False, encoding="utf-8-sig")
        webtoon_df.to_excel(RAW_XLSX, index=False)

        print("[2.5단계] 기존 productNo 매핑 재사용/신규 검색 대상 분리")
        merged_df, recheck_df = merge_current_with_existing(webtoon_df, existing_lookup)
        print(f"[재사용] {len(merged_df) - len(recheck_df)}개")
        print(f"[재검색 대상] {len(recheck_df)}개")

        final_df = search_missing_series(driver, merged_df)
        final_df = clean_series_title_columns(final_df)

        # productNo가 있으면 series_url 보정
        if "series_product_no" in final_df.columns:
            final_df["series_product_no"] = final_df["series_product_no"].apply(norm_id)
            if "series_url" not in final_df.columns:
                final_df["series_url"] = ""
            empty_url = final_df["series_url"].fillna("").astype(str).str.strip().eq("") & final_df["series_product_no"].ne("")
            final_df.loc[empty_url, "series_url"] = final_df.loc[empty_url, "series_product_no"].apply(series_url_from_product_no)

        final_df["mapping_updated_at_kst"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")

        # adult/login은 별도 분류만 하고, final mapping에서 제거하지 않음
        adult_mask = pd.Series(False, index=final_df.index)

        if "author_collect_status" in final_df.columns:
            adult_mask = adult_mask | final_df["author_collect_status"].astype(str).str.contains("login_or_adult", case=False, na=False)

        if "download_status" in final_df.columns:
            adult_mask = adult_mask | final_df["download_status"].astype(str).str.contains("adult|login", case=False, na=False)

        adult_df = final_df[adult_mask].copy()

        duplicate_df = (
            final_df[final_df.duplicated(subset=["webtoon_title_id"], keep=False)].copy()
            if "webtoon_title_id" in final_df.columns
            else pd.DataFrame()
        )

        final_df.to_csv(MAPPING_CSV, index=False, encoding="utf-8-sig")
        final_df.to_excel(MAPPING_XLSX, index=False)

        adult_df.to_csv(ADULT_CSV, index=False, encoding="utf-8-sig")
        adult_df.to_excel(ADULT_XLSX, index=False)

        duplicate_df.to_csv(DUPLICATE_CSV, index=False, encoding="utf-8-sig")
        duplicate_df.to_excel(DUPLICATE_XLSX, index=False)

        print(f"[저장] mapping rows={len(final_df)}: {MAPPING_CSV}")
        print(f"[저장] adult/login rows={len(adult_df)}: {ADULT_CSV}")
        print(f"[저장] duplicate rows={len(duplicate_df)}: {DUPLICATE_CSV}")

        service = get_sheets_service()

        replace_df_to_sheet(service, GOOGLE_SHEETS_ID, "weekly_mapping_raw", webtoon_df)
        replace_df_to_sheet(service, GOOGLE_SHEETS_ID, "weekly_mapping", final_df)
        replace_df_to_sheet(service, GOOGLE_SHEETS_ID, "adult_login_required", adult_df)
        replace_df_to_sheet(service, GOOGLE_SHEETS_ID, "mapping_duplicate_rows", duplicate_df)
        replace_df_to_sheet(service, GOOGLE_SHEETS_ID, "weekly_recheck_targets", recheck_df)

        finished_at = now_kst()

        log_row = {
            "mapping_updated_at_kst": finished_at.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round((finished_at - started_at).total_seconds(), 2),
            "webtoon_rows": len(webtoon_df),
            "mapping_rows": len(final_df),
            "reused_existing_rows": int((final_df.get("mapping_source", "") == "reused_existing_product_no").sum()) if "mapping_source" in final_df.columns else "",
            "searched_rows": int((final_df.get("mapping_source", "") == "searched_series").sum()) if "mapping_source" in final_df.columns else "",
            "adult_rows": len(adult_df),
            "duplicate_rows": len(duplicate_df),
            "matched_rows": int((final_df.get("match_status", "") == "matched").sum()) if "match_status" in final_df.columns else "",
            "review_needed_rows": int((final_df.get("match_status", "") == "review_needed").sum()) if "match_status" in final_df.columns else "",
            "no_result_rows": int((final_df.get("match_status", "") == "no_result").sum()) if "match_status" in final_df.columns else "",
            "product_no_blank_rows": int(final_df.get("series_product_no", pd.Series([""] * len(final_df))).fillna("").astype(str).str.strip().eq("").sum()) if len(final_df) else "",
            "force_recheck_all": FORCE_RECHECK_ALL,
        }

        append_log_to_sheet(service, GOOGLE_SHEETS_ID, log_row)

        print("[주간 매핑 업데이트] 완료")
        print(log_row)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
