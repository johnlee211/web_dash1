from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# =========================================================
# 기본 설정
# =========================================================

st.set_page_config(page_title="시리즈 다운로드 수집 대시보드", layout="wide")

BASE_DIR = Path(__file__).resolve().parent
DATA_CANDIDATES = [
    BASE_DIR / "series_download_increase_report_history.csv",
    BASE_DIR / "data" / "series_download_increase_report_history.csv",
    BASE_DIR / "series_download_increase_report_history.xlsx",
    BASE_DIR / "data" / "series_download_increase_report_history.xlsx",
]

REQUIRED_POST_COLS = ["webtoon_title", "collected_at_kst", "series_download_count"]
REQUIRED_BASELINE_COLS = ["baseline_collected_at_kst", "baseline_download_count"]


# =========================================================
# 유틸
# =========================================================

def read_table(path_or_file) -> pd.DataFrame:
    name = getattr(path_or_file, "name", str(path_or_file))
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        for enc in ["utf-8-sig", "utf-8", "cp949"]:
            try:
                return pd.read_csv(path_or_file, encoding=enc)
            except Exception:
                continue
        return pd.read_csv(path_or_file)
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(path_or_file)
    raise ValueError(f"지원하지 않는 파일 형식입니다: {name}")


def find_default_file() -> Optional[Path]:
    for p in DATA_CANDIDATES:
        if p.exists():
            return p
    return None


def parse_number(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def fmt_int(x) -> str:
    if pd.isna(x):
        return "-"
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", str(name))
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name or "download_data"


@st.cache_data(show_spinner="데이터를 읽는 중입니다...")
def load_data_from_path(path: str) -> pd.DataFrame:
    return read_table(Path(path))


@st.cache_data(show_spinner="업로드 파일을 읽는 중입니다...")
def load_data_from_upload(uploaded_file) -> pd.DataFrame:
    return read_table(uploaded_file)


def standardize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # 날짜/숫자 정리
    for col in ["collected_at_kst", "baseline_collected_at_kst"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")

    for col in ["series_download_count", "baseline_download_count", "download_increase", "download_increase_rate"]:
        if col in out.columns:
            out[col] = parse_number(out[col])

    # 표시용 기본 컬럼 보완
    for col in ["webtoon_title", "release_weekday_ko", "release_weekday", "release_event_key"]:
        if col not in out.columns:
            out[col] = ""

    # 증가량 없으면 baseline/post 차이로 계산
    if "download_increase" not in out.columns:
        out["download_increase"] = np.nan
    if "baseline_download_count" in out.columns:
        mask = out["download_increase"].isna() & out["series_download_count"].notna() & out["baseline_download_count"].notna()
        out.loc[mask, "download_increase"] = out.loc[mask, "series_download_count"] - out.loc[mask, "baseline_download_count"]

    # 같은 공개 이벤트가 중복 저장된 경우 최신 사후수집만 유지
    if "collected_at_kst" in out.columns:
        out = out.sort_values("collected_at_kst")
    dedupe_cols = [c for c in ["collected_date_kst", "release_event_key", "webtoon_title"] if c in out.columns]
    if "release_event_key" in out.columns and "collected_date_kst" in out.columns:
        out = out.drop_duplicates(dedupe_cols, keep="last")

    return out.reset_index(drop=True)


def validate(df: pd.DataFrame):
    missing = [c for c in REQUIRED_POST_COLS if c not in df.columns]
    if missing:
        st.error("필수 컬럼이 없습니다: " + ", ".join(missing))
        st.stop()


def title_options(df: pd.DataFrame) -> list[str]:
    titles = df["webtoon_title"].dropna().astype(str).str.strip()
    titles = sorted([t for t in titles.unique().tolist() if t and t.lower() != "nan"])
    return titles


def build_event_table(df: pd.DataFrame, title: str) -> pd.DataFrame:
    d = df[df["webtoon_title"].astype(str).str.strip() == title].copy()
    if d.empty:
        return pd.DataFrame()

    # 사전/사후가 모두 있는 이벤트 중심 테이블
    keep = {
        "release_weekday_ko": "공개요일",
        "baseline_collected_at_kst": "사전수집시각",
        "baseline_download_count": "사전 누적 다운로드수",
        "collected_at_kst": "사후수집시각",
        "series_download_count": "사후 누적 다운로드수",
        "download_increase": "공개 전후 증가수",
    }
    cols = [c for c in keep.keys() if c in d.columns]
    tbl = d[cols].rename(columns=keep)

    if "사전수집시각" in tbl.columns:
        tbl = tbl[tbl["사전수집시각"].notna()].copy()
    if tbl.empty:
        # baseline이 없는 첫 수집분도 숨기지 않기 위한 예외 처리
        keep_no_base = {
            "release_weekday_ko": "공개요일",
            "collected_at_kst": "사후수집시각",
            "series_download_count": "사후 누적 다운로드수",
        }
        cols2 = [c for c in keep_no_base.keys() if c in d.columns]
        tbl = d[cols2].rename(columns=keep_no_base)

    sort_col = "사후수집시각" if "사후수집시각" in tbl.columns else ("사전수집시각" if "사전수집시각" in tbl.columns else None)
    if sort_col:
        tbl = tbl.sort_values(sort_col)
    return tbl.reset_index(drop=True)


def build_point_table(events: pd.DataFrame, title: str) -> pd.DataFrame:
    rows = []
    if events.empty:
        return pd.DataFrame(columns=["수집시각", "누적 다운로드수", "수집구분", "공개요일", "공개 전후 증가수"])

    for idx, r in events.iterrows():
        release_day = r.get("공개요일", "")
        inc = r.get("공개 전후 증가수", np.nan)

        pre_dt = r.get("사전수집시각", pd.NaT)
        pre_cnt = r.get("사전 누적 다운로드수", np.nan)
        if pd.notna(pre_dt) and pd.notna(pre_cnt):
            rows.append({
                "수집시각": pre_dt,
                "누적 다운로드수": pre_cnt,
                "수집구분": "사전수집",
                "공개요일": release_day,
                "공개 전후 증가수": inc,
                "웹툰": title,
                "이벤트순번": idx + 1,
            })

        post_dt = r.get("사후수집시각", pd.NaT)
        post_cnt = r.get("사후 누적 다운로드수", np.nan)
        if pd.notna(post_dt) and pd.notna(post_cnt):
            rows.append({
                "수집시각": post_dt,
                "누적 다운로드수": post_cnt,
                "수집구분": "사후수집",
                "공개요일": release_day,
                "공개 전후 증가수": inc,
                "웹툰": title,
                "이벤트순번": idx + 1,
            })

    pts = pd.DataFrame(rows)
    if pts.empty:
        return pts
    pts["수집시각"] = pd.to_datetime(pts["수집시각"], errors="coerce")
    pts["누적 다운로드수"] = pd.to_numeric(pts["누적 다운로드수"], errors="coerce")
    pts["공개 전후 증가수"] = pd.to_numeric(pts["공개 전후 증가수"], errors="coerce")
    pts = pts.dropna(subset=["수집시각", "누적 다운로드수"]).sort_values("수집시각").reset_index(drop=True)
    return pts


def plot_prepost_line(points: pd.DataFrame, title: str):
    if points.empty:
        st.info("표시할 수집 시점 데이터가 없습니다.")
        return

    fig = go.Figure()

    # 전체 누적 다운로드 흐름
    fig.add_trace(go.Scatter(
        x=points["수집시각"],
        y=points["누적 다운로드수"],
        mode="lines",
        name="누적 다운로드 흐름",
        line=dict(width=2.6),
        hoverinfo="skip",
    ))

    # 사전/사후 마커 분리
    marker_specs = {
        "사전수집": dict(symbol="circle", size=10),
        "사후수집": dict(symbol="diamond", size=11),
    }
    for phase in ["사전수집", "사후수집"]:
        g = points[points["수집구분"] == phase].copy()
        if g.empty:
            continue
        custom = np.stack([
            g["수집구분"].astype(str),
            g["공개요일"].fillna("").astype(str),
            g["공개 전후 증가수"].map(fmt_int),
        ], axis=-1)
        fig.add_trace(go.Scatter(
            x=g["수집시각"],
            y=g["누적 다운로드수"],
            mode="markers",
            name=phase,
            marker=marker_specs[phase],
            customdata=custom,
            hovertemplate=(
                "수집시각=%{x|%Y-%m-%d %H:%M}<br>"
                "구분=%{customdata[0]}<br>"
                "공개요일=%{customdata[1]}<br>"
                "누적 다운로드수=%{y:,.0f}<br>"
                "공개 전후 증가수=%{customdata[2]}"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=f"{title} — 사전/사후 수집 시점별 누적 다운로드수",
        height=560,
        margin=dict(t=80, b=70, l=80, r=40),
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        xaxis=dict(title="수집 시각", showgrid=True),
        yaxis=dict(title="누적 다운로드수", tickformat=","),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def plot_increase_bar(events: pd.DataFrame, title: str):
    if events.empty or "공개 전후 증가수" not in events.columns:
        return
    d = events.dropna(subset=["공개 전후 증가수"]).copy()
    if d.empty:
        return
    x = d["사후수집시각"] if "사후수집시각" in d.columns else d.index
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x,
        y=d["공개 전후 증가수"],
        name="공개 전후 증가수",
        hovertemplate="사후수집=%{x}<br>증가수=%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        title=f"{title} — 공개 전후 증가수",
        height=320,
        margin=dict(t=70, b=50, l=80, r=40),
        xaxis=dict(title="사후 수집 시각"),
        yaxis=dict(title="증가수", tickformat=","),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def show_metrics(points: pd.DataFrame, events: pd.DataFrame):
    latest_download = points["누적 다운로드수"].dropna().iloc[-1] if not points.empty else np.nan
    total_increase = events["공개 전후 증가수"].dropna().sum() if "공개 전후 증가수" in events.columns else np.nan
    event_count = len(events)
    c1, c2, c3 = st.columns(3)
    c1.metric("최근 누적 다운로드수", fmt_int(latest_download))
    c2.metric("공개 전후 증가수 합계", fmt_int(total_increase))
    c3.metric("공개 이벤트 수", f"{event_count:,}")


# =========================================================
# 화면
# =========================================================

st.title("시리즈 다운로드 수집 대시보드")
st.caption("사전수집과 사후수집을 같은 누적 다운로드수 라인차트 위에 표시합니다.")

file_path = find_default_file()
if file_path:
    raw = load_data_from_path(str(file_path))
else:
    uploaded = st.file_uploader("series_download_increase_report_history 파일 업로드", type=["csv", "xlsx", "xls"])
    if uploaded is None:
        st.info("`series_download_increase_report_history.csv` 파일을 이 대시보드 파일과 같은 폴더 또는 data 폴더에 두면 자동으로 읽습니다.")
        st.stop()
    raw = load_data_from_upload(uploaded)

validate(raw)
df = standardize(raw)

opts = title_options(df)
if not opts:
    st.error("웹툰명을 찾지 못했습니다. webtoon_title 컬럼을 확인해주세요.")
    st.stop()

selected = st.selectbox("웹툰 선택", opts, index=0)

events_tbl = build_event_table(df, selected)
points_tbl = build_point_table(events_tbl, selected)

show_metrics(points_tbl, events_tbl)
st.divider()

plot_prepost_line(points_tbl, selected)
plot_increase_bar(events_tbl, selected)

st.markdown("### 공개 전후 수집 데이터")
if events_tbl.empty:
    st.info("표시할 데이터가 없습니다.")
else:
    display = events_tbl.copy()
    for col in ["사전수집시각", "사후수집시각"]:
        if col in display.columns:
            display[col] = pd.to_datetime(display[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
    for col in ["사전 누적 다운로드수", "사후 누적 다운로드수", "공개 전후 증가수"]:
        if col in display.columns:
            display[col] = pd.to_numeric(display[col], errors="coerce").map(fmt_int)
    st.dataframe(display, use_container_width=True, hide_index=True)

    csv_df = events_tbl.copy()
    st.download_button(
        "CSV 다운로드",
        data=csv_df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"{safe_filename(selected)}_download_prepost.csv",
        mime="text/csv",
    )
