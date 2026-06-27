from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ════════════════════════════════════════════════════════════
# 페이지 설정
# ════════════════════════════════════════════════════════════

st.set_page_config(page_title="시리즈 다운로드 수 대시보드", layout="wide")

BASE_DIR = Path(__file__).parent
SEARCH_DIRS = [
    BASE_DIR,
    BASE_DIR / "data",
    BASE_DIR / "dashboard_data",
]

WEEKDAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_MAP = {
    "mon": "월", "monday": "월", "월": "월",
    "tue": "화", "tues": "화", "tuesday": "화", "화": "화",
    "wed": "수", "wednesday": "수", "수": "수",
    "thu": "목", "thur": "목", "thurs": "목", "thursday": "목", "목": "목",
    "fri": "금", "friday": "금", "금": "금",
    "sat": "토", "saturday": "토", "토": "토",
    "sun": "일", "sunday": "일", "일": "일",
}

# ════════════════════════════════════════════════════════════
# 유틸
# ════════════════════════════════════════════════════════════

def parse_number(x):
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    text = str(x).strip().replace(",", "")
    if text == "":
        return np.nan
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    if not nums:
        return np.nan
    try:
        return float(nums[-1])
    except Exception:
        return np.nan


def safe_str(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def normalize_weekday(x) -> str:
    text = safe_str(x).lower()
    return WEEKDAY_MAP.get(text, safe_str(x))


def safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", str(name))
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name or "download_dashboard"


def render_plotly(fig: go.Figure, title_for_download: Optional[str] = None, height: Optional[int] = None):
    if height is not None:
        fig.update_layout(height=height)
    fig.update_layout(
        margin=dict(t=70, b=50, l=60, r=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)
    title = title_for_download or "plotly_chart"
    st.download_button(
        "차트 HTML 다운로드",
        data=fig.to_html(include_plotlyjs="cdn", full_html=True),
        file_name=f"{safe_filename(title)}.html",
        mime="text/html",
        key=f"download_{safe_filename(title)}_{abs(hash(title))}",
    )


def format_int(x) -> str:
    if pd.isna(x):
        return "-"
    return f"{int(round(float(x))):,}"


def format_rate(x) -> str:
    if pd.isna(x):
        return "-"
    return f"{float(x):.2f}%"


def iter_candidate_files() -> List[Path]:
    paths = []
    patterns = [
        "series_download_increase_report_history*.csv",
        "series_download_increase_report*.csv",
        "*download*history*.csv",
        "*다운로드*history*.csv",
        "*.csv",
    ]
    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        for pat in patterns:
            paths.extend(d.glob(pat))
    seen = set()
    unique = []
    for p in paths:
        try:
            resolved = p.resolve()
        except Exception:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(p)
    return sorted(unique, key=lambda p: ("download" not in p.name.lower(), p.name.lower()))


def read_csv_flexible(path_or_file) -> pd.DataFrame:
    for enc in ["utf-8-sig", "utf-8", "cp949"]:
        try:
            return pd.read_csv(path_or_file, encoding=enc)
        except Exception:
            continue
    return pd.read_csv(path_or_file)


@st.cache_data(show_spinner="다운로드 수 데이터를 읽는 중입니다...")
def load_data_from_path(path_str: str) -> pd.DataFrame:
    return read_csv_flexible(path_str)


@st.cache_data(show_spinner="업로드 파일을 읽는 중입니다...")
def load_data_from_upload(uploaded_file) -> pd.DataFrame:
    return read_csv_flexible(uploaded_file)


def standardize_download_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()

    # 컬럼명 보정: 기존 산출물과 향후 산출물 양쪽을 받기 위한 최소 매핑
    rename_map = {
        "title": "webtoon_title",
        "웹툰명": "webtoon_title",
        "작품명": "webtoon_title",
        "download_count": "series_download_count",
        "다운로드수": "series_download_count",
        "increase": "download_increase",
        "증가량": "download_increase",
        "collected_at": "collected_at_kst",
        "수집시각": "collected_at_kst",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = [
        "webtoon_title", "webtoon_author", "webtoon_title_id", "series_product_no",
        "series_title", "series_url", "release_weekday_ko", "weekday", "collection_context",
        "match_status", "download_status", "series_download_count", "baseline_download_count",
        "download_increase", "download_increase_rate", "snapshot_id", "release_event_key",
        "collected_at_kst", "dt", "series_download_text", "baseline_download_text",
        "series_result_text",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    for col in ["series_download_count", "baseline_download_count", "download_increase", "download_increase_rate"]:
        df[col] = df[col].apply(parse_number)

    # 수집 시각: collected_at_kst 우선, 없으면 dt 사용
    collected = pd.to_datetime(df["collected_at_kst"], errors="coerce")
    fallback = pd.to_datetime(df["dt"], errors="coerce")
    df["collected_at"] = collected.fillna(fallback)
    df["collected_date"] = df["collected_at"].dt.date

    # 월요일 시작 주차
    df["week_start"] = (df["collected_at"] - pd.to_timedelta(df["collected_at"].dt.weekday, unit="D")).dt.normalize()
    df["week_label"] = df["week_start"].dt.strftime("%Y-%m-%d 주")

    # 요일 정규화
    release_wd = df["release_weekday_ko"].map(normalize_weekday)
    fallback_wd = df["weekday"].map(normalize_weekday)
    df["release_weekday_label"] = release_wd.replace("", np.nan).fillna(fallback_wd)
    df["release_weekday_label"] = df["release_weekday_label"].replace("", "미상")

    for col in ["webtoon_title", "series_title", "collection_context", "match_status", "download_status", "series_url"]:
        df[col] = df[col].fillna("").astype(str).str.strip()

    # 중복 제거: 같은 스냅샷/이벤트/작품이 중복 저장된 경우 마지막 수집값 유지
    df = df.sort_values("collected_at")
    dedupe_cols = [
        c for c in [
            "webtoon_title", "series_product_no", "snapshot_id", "release_event_key", "collection_context"
        ] if c in df.columns
    ]
    if dedupe_cols:
        df = df.drop_duplicates(subset=dedupe_cols, keep="last")

    # 이전 수집 대비 증가량
    group_key = "series_product_no"
    if df[group_key].isna().all() or (df[group_key].astype(str).str.strip() == "").all():
        group_key = "webtoon_title"
    df = df.sort_values([group_key, "collected_at"])
    df["diff_from_prev_collect"] = df.groupby(group_key, dropna=False)["series_download_count"].diff()
    df["diff_from_prev_collect"] = df["diff_from_prev_collect"].where(df["diff_from_prev_collect"] >= 0)

    # 증가량 기본값: 수집기가 만든 download_increase가 없으면 diff_from_prev_collect 사용
    df["download_increase_clean"] = df["download_increase"].fillna(df["diff_from_prev_collect"])
    df["download_increase_clean"] = df["download_increase_clean"].fillna(0)

    # 증가율 보정
    df["download_increase_rate_clean"] = df["download_increase_rate"]
    mask = df["download_increase_rate_clean"].isna() & (df["baseline_download_count"] > 0)
    df.loc[mask, "download_increase_rate_clean"] = df.loc[mask, "download_increase_clean"] / df.loc[mask, "baseline_download_count"] * 100

    return df.reset_index(drop=True)


def apply_global_filters(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    with st.sidebar:
        st.header("필터")
        ok_only = st.checkbox("수집 성공 데이터만 보기", value=True)
        matched_only = st.checkbox("매칭 성공 데이터만 보기", value=True)

        if ok_only and "download_status" in out.columns:
            out = out[out["download_status"].str.lower().eq("ok")]
        if matched_only and "match_status" in out.columns:
            out = out[out["match_status"].str.lower().eq("matched")]

        if out["collected_at"].notna().any():
            min_date = out["collected_at"].min().date()
            max_date = out["collected_at"].max().date()
            date_range = st.date_input("수집일 범위", value=(min_date, max_date), min_value=min_date, max_value=max_date)
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start, end = date_range
                out = out[(out["collected_at"].dt.date >= start) & (out["collected_at"].dt.date <= end)]

        weekdays = [w for w in WEEKDAY_ORDER if w in set(out["release_weekday_label"].dropna())]
        extra = sorted([w for w in out["release_weekday_label"].dropna().unique() if w not in WEEKDAY_ORDER])
        weekday_options = weekdays + extra
        selected_weekdays = st.multiselect("연재 요일", weekday_options, default=weekday_options)
        if selected_weekdays:
            out = out[out["release_weekday_label"].isin(selected_weekdays)]

        contexts = sorted([x for x in out["collection_context"].dropna().unique().tolist() if str(x).strip()])
        selected_contexts = st.multiselect("수집 맥락", contexts, default=contexts)
        if selected_contexts:
            out = out[out["collection_context"].isin(selected_contexts)]

        metric_mode = st.radio(
            "증가량 기준",
            ["리포트 증가량", "이전 수집 대비 증가량"],
            index=0,
            help="리포트 증가량은 수집기가 계산한 baseline 대비 증가량입니다. 이전 수집 대비 증가량은 누적 다운로드 수의 직전 수집값 대비 차이입니다.",
        )
        st.session_state["metric_col"] = "download_increase_clean" if metric_mode == "리포트 증가량" else "diff_from_prev_collect"

    return out


def show_kpis(df: pd.DataFrame, metric_col: str):
    total_webtoons = df["webtoon_title"].replace("", np.nan).nunique()
    snapshots = df["snapshot_id"].replace("", np.nan).nunique() if "snapshot_id" in df.columns else 0
    total_inc = pd.to_numeric(df[metric_col], errors="coerce").fillna(0).sum()
    avg_inc = pd.to_numeric(df[metric_col], errors="coerce").replace([np.inf, -np.inf], np.nan).mean()
    latest_total = df.sort_values("collected_at").groupby("webtoon_title")["series_download_count"].last().sum()
    ok_rate = (df["download_status"].str.lower().eq("ok").mean() * 100) if len(df) else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("웹툰 수", f"{total_webtoons:,}")
    c2.metric("스냅샷 수", f"{snapshots:,}")
    c3.metric("총 증가량", format_int(total_inc))
    c4.metric("평균 증가량", format_int(avg_inc))
    c5.metric("최신 누적합", format_int(latest_total))
    c6.metric("수집 성공률", format_rate(ok_rate))


def aggregate_latest_by_title(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    latest = df.sort_values("collected_at").groupby("webtoon_title", dropna=False).tail(1)
    return latest[["webtoon_title", "series_title", "series_download_count", "collected_at", "release_weekday_label", "series_url"]].copy()


def ranking_by_title(df: pd.DataFrame, metric_col: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    metric = pd.to_numeric(df[metric_col], errors="coerce").fillna(0)
    work = df.copy()
    work["metric_value"] = metric
    latest = aggregate_latest_by_title(work).rename(columns={"series_download_count": "latest_download_count", "collected_at": "latest_collected_at"})
    agg = work.groupby("webtoon_title", dropna=False).agg(
        total_increase=("metric_value", "sum"),
        avg_increase=("metric_value", "mean"),
        median_increase=("metric_value", "median"),
        max_increase=("metric_value", "max"),
        snapshot_count=("snapshot_id", "nunique"),
        collect_count=("collected_at", "count"),
        release_weekday_label=("release_weekday_label", lambda x: ",".join(sorted(set([v for v in x if v]))[:3])),
        avg_increase_rate=("download_increase_rate_clean", "mean"),
    ).reset_index()
    out = agg.merge(latest[["webtoon_title", "latest_download_count", "latest_collected_at", "series_url"]], on="webtoon_title", how="left")
    return out.sort_values("total_increase", ascending=False)


def styled_table(df: pd.DataFrame):
    st.dataframe(df, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════
# 데이터 로드
# ════════════════════════════════════════════════════════════

st.sidebar.header("데이터")
candidates = iter_candidate_files()
uploaded = st.sidebar.file_uploader("CSV 업로드", type=["csv"])

if uploaded is not None:
    raw_df = load_data_from_upload(uploaded)
    source_label = uploaded.name
else:
    if not candidates:
        st.title("시리즈 다운로드 수 대시보드")
        st.info("CSV 파일을 찾지 못했습니다. `series_download_increase_report_history.csv`를 이 파일과 같은 폴더에 두거나 사이드바에서 업로드해주세요.")
        st.stop()
    selected_path = st.sidebar.selectbox("사용할 CSV 파일", candidates, format_func=lambda p: p.name)
    raw_df = load_data_from_path(str(selected_path))
    source_label = selected_path.name

all_df = standardize_download_data(raw_df)
filtered_df = apply_global_filters(all_df)
metric_col = st.session_state.get("metric_col", "download_increase_clean")
metric_label = "리포트 증가량" if metric_col == "download_increase_clean" else "이전 수집 대비 증가량"

st.title("시리즈 다운로드 수 대시보드")
st.caption(f"데이터 파일: `{source_label}` · 증가량 기준: **{metric_label}**")

if filtered_df.empty:
    st.warning("필터 조건에 맞는 데이터가 없습니다.")
    st.stop()

show_kpis(filtered_df, metric_col)
st.divider()

tab_overview, tab_week, tab_title, tab_weekday, tab_rank, tab_review = st.tabs([
    "데이터 개요", "주별 증가량", "웹툰별 상세", "요일별 분석", "TOP 랭킹", "검수 데이터"
])

# ════════════════════════════════════════════════════════════
# 데이터 개요
# ════════════════════════════════════════════════════════════

with tab_overview:
    st.subheader("수집 데이터 개요")
    c1, c2 = st.columns([1.25, 1])
    with c1:
        daily = filtered_df.groupby(filtered_df["collected_at"].dt.date).agg(
            수집건수=("webtoon_title", "size"),
            웹툰수=("webtoon_title", "nunique"),
            증가량=(metric_col, "sum"),
        ).reset_index().rename(columns={"collected_at": "수집일"})
        daily.columns = ["수집일", "수집건수", "웹툰수", "증가량"]
        fig = px.line(daily, x="수집일", y="증가량", markers=True, title="일자별 다운로드 증가량")
        fig.update_yaxes(title="다운로드 증가량")
        render_plotly(fig, "일자별_다운로드_증가량", height=420)
    with c2:
        status = filtered_df.groupby(["match_status", "download_status"]).size().reset_index(name="count")
        fig = px.bar(status, x="download_status", y="count", color="match_status", title="수집 상태 분포", text="count")
        fig.update_xaxes(title="다운로드 상태")
        fig.update_yaxes(title="건수")
        render_plotly(fig, "수집_상태_분포", height=420)

    st.markdown("#### 최신 수집 기준 웹툰 목록")
    latest = aggregate_latest_by_title(filtered_df)
    latest_display = latest.rename(columns={
        "webtoon_title": "웹툰명",
        "series_title": "시리즈명",
        "series_download_count": "최신 누적 다운로드",
        "collected_at": "최신 수집시각",
        "release_weekday_label": "연재요일",
        "series_url": "시리즈 URL",
    })
    styled_table(latest_display.sort_values("최신 누적 다운로드", ascending=False).head(300))

# ════════════════════════════════════════════════════════════
# 주별 증가량
# ════════════════════════════════════════════════════════════

with tab_week:
    st.subheader("주별 다운로드 증가량")
    weekly = filtered_df.groupby(["week_start", "week_label"]).agg(
        total_increase=(metric_col, "sum"),
        avg_increase=(metric_col, "mean"),
        median_increase=(metric_col, "median"),
        webtoon_count=("webtoon_title", "nunique"),
        collect_count=("webtoon_title", "size"),
    ).reset_index().sort_values("week_start")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=weekly["week_label"], y=weekly["total_increase"], mode="lines+markers", name="주간 증가량",
        hovertemplate="주차=%{x}<br>증가량=%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(title="주별 전체 다운로드 증가량")
    fig.update_xaxes(title="주차")
    fig.update_yaxes(title="다운로드 증가량")
    render_plotly(fig, "주별_전체_다운로드_증가량", height=430)

    st.markdown("#### 선택 주차 웹툰별 TOP")
    week_labels = weekly["week_label"].tolist()
    selected_week = st.selectbox("주차 선택", week_labels, index=max(0, len(week_labels) - 1), key="weekly_selected_week")
    top_n = st.slider("TOP N", 5, 50, 20, step=5, key="weekly_top_n")
    week_df = filtered_df[filtered_df["week_label"] == selected_week]
    rank = ranking_by_title(week_df, metric_col).head(top_n)
    if rank.empty:
        st.info("선택한 주차에 표시할 데이터가 없습니다.")
    else:
        fig = px.bar(
            rank.sort_values("total_increase"),
            x="total_increase", y="webtoon_title", orientation="h",
            title=f"{selected_week} 웹툰별 다운로드 증가량 TOP {top_n}",
            hover_data=["latest_download_count", "avg_increase", "release_weekday_label"],
        )
        fig.update_xaxes(title="다운로드 증가량")
        fig.update_yaxes(title="웹툰")
        render_plotly(fig, f"{selected_week}_웹툰별_TOP", height=max(430, top_n * 24 + 160))
        display = rank.rename(columns={
            "webtoon_title": "웹툰명",
            "release_weekday_label": "연재요일",
            "total_increase": "주간 증가량",
            "avg_increase": "평균 증가량",
            "median_increase": "중앙값 증가량",
            "max_increase": "최대 증가량",
            "latest_download_count": "최신 누적 다운로드",
            "collect_count": "수집 건수",
            "avg_increase_rate": "평균 증가율",
        })
        show_cols = ["웹툰명", "연재요일", "주간 증가량", "평균 증가량", "중앙값 증가량", "최대 증가량", "최신 누적 다운로드", "수집 건수", "평균 증가율"]
        styled_table(display[[c for c in show_cols if c in display.columns]])

    with st.expander("주별 집계 테이블"):
        display = weekly.rename(columns={
            "week_label": "주차", "total_increase": "주간 증가량", "avg_increase": "평균 증가량",
            "median_increase": "중앙값 증가량", "webtoon_count": "웹툰 수", "collect_count": "수집 건수",
        })
        styled_table(display[["주차", "웹툰 수", "수집 건수", "주간 증가량", "평균 증가량", "중앙값 증가량"]])

# ════════════════════════════════════════════════════════════
# 웹툰별 상세
# ════════════════════════════════════════════════════════════

with tab_title:
    st.subheader("웹툰별 수집 시점 추이")
    titles = sorted(filtered_df["webtoon_title"].replace("", np.nan).dropna().unique().tolist())
    selected_title = st.selectbox("웹툰 선택", titles, key="title_detail_select")
    one = filtered_df[filtered_df["webtoon_title"] == selected_title].sort_values("collected_at").copy()
    one["metric_value"] = pd.to_numeric(one[metric_col], errors="coerce").fillna(0)

    latest_row = one.dropna(subset=["collected_at"]).tail(1)
    latest_download = latest_row["series_download_count"].iloc[0] if not latest_row.empty else np.nan
    latest_time = latest_row["collected_at"].iloc[0] if not latest_row.empty else None

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("최신 누적 다운로드", format_int(latest_download))
    c2.metric("기간 내 증가량", format_int(one["metric_value"].sum()))
    c3.metric("평균 증가량", format_int(one["metric_value"].mean()))
    c4.metric("수집 건수", f"{len(one):,}")
    c5.metric("최신 수집", latest_time.strftime("%Y-%m-%d %H:%M") if pd.notna(latest_time) else "-")

    left, right = st.columns(2)
    with left:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=one["collected_at"], y=one["series_download_count"], mode="lines+markers", name="누적 다운로드",
            hovertemplate="수집시각=%{x}<br>누적 다운로드=%{y:,.0f}<extra></extra>",
        ))
        fig.update_layout(title=f"{selected_title} 수집 시점별 누적 다운로드")
        fig.update_xaxes(title="수집 시각")
        fig.update_yaxes(title="누적 다운로드 수")
        render_plotly(fig, f"{selected_title}_누적_다운로드", height=430)
    with right:
        fig = px.bar(
            one, x="collected_at", y="metric_value", title=f"{selected_title} 수집 시점별 다운로드 증가량",
            hover_data=["series_download_count", "baseline_download_count", "collection_context", "release_weekday_label"],
        )
        fig.update_xaxes(title="수집 시각")
        fig.update_yaxes(title="다운로드 증가량")
        render_plotly(fig, f"{selected_title}_수집시점별_증가량", height=430)

    st.markdown("#### 주별 추이")
    one_week = one.groupby(["week_start", "week_label"]).agg(
        weekly_increase=("metric_value", "sum"),
        weekly_avg=("metric_value", "mean"),
        latest_download=("series_download_count", "last"),
        collect_count=("collected_at", "count"),
    ).reset_index().sort_values("week_start")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=one_week["week_label"], y=one_week["weekly_increase"], name="주간 증가량"))
    fig.add_trace(go.Scatter(x=one_week["week_label"], y=one_week["latest_download"], name="주차별 최신 누적", yaxis="y2", mode="lines+markers"))
    fig.update_layout(
        title=f"{selected_title} 주별 증가량과 누적 다운로드",
        yaxis=dict(title="주간 증가량"),
        yaxis2=dict(title="누적 다운로드", overlaying="y", side="right"),
    )
    fig.update_xaxes(title="주차")
    render_plotly(fig, f"{selected_title}_주별_추이", height=430)

    with st.expander("수집 원자료 보기", expanded=False):
        show_cols = [
            "collected_at", "week_label", "release_weekday_label", "collection_context", "series_download_count",
            "baseline_download_count", "download_increase", "diff_from_prev_collect", "download_increase_rate_clean",
            "match_status", "download_status", "series_url",
        ]
        display = one[[c for c in show_cols if c in one.columns]].rename(columns={
            "collected_at": "수집시각", "week_label": "주차", "release_weekday_label": "연재요일",
            "collection_context": "수집맥락", "series_download_count": "누적 다운로드",
            "baseline_download_count": "기준 다운로드", "download_increase": "리포트 증가량",
            "diff_from_prev_collect": "이전 수집 대비 증가량", "download_increase_rate_clean": "증가율",
            "match_status": "매칭상태", "download_status": "다운로드상태", "series_url": "시리즈 URL",
        })
        styled_table(display)
        st.download_button(
            "선택 웹툰 데이터 CSV 다운로드",
            data=display.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{safe_filename(selected_title)}_download_detail.csv",
            mime="text/csv",
        )

# ════════════════════════════════════════════════════════════
# 요일별 분석
# ════════════════════════════════════════════════════════════

with tab_weekday:
    st.subheader("연재 요일별 다운로드 증가")
    wd = filtered_df.copy()
    wd["metric_value"] = pd.to_numeric(wd[metric_col], errors="coerce").fillna(0)
    weekday_summary = wd.groupby("release_weekday_label").agg(
        total_increase=("metric_value", "sum"),
        avg_increase=("metric_value", "mean"),
        median_increase=("metric_value", "median"),
        webtoon_count=("webtoon_title", "nunique"),
        collect_count=("webtoon_title", "size"),
    ).reset_index()
    weekday_summary["weekday_order"] = weekday_summary["release_weekday_label"].map({w: i for i, w in enumerate(WEEKDAY_ORDER)}).fillna(99)
    weekday_summary = weekday_summary.sort_values("weekday_order")

    metric_option = st.selectbox(
        "요일별 차트 지표",
        ["total_increase", "avg_increase", "median_increase", "webtoon_count"],
        format_func=lambda x: {
            "total_increase": "증가량 합계",
            "avg_increase": "평균 증가량",
            "median_increase": "중앙값 증가량",
            "webtoon_count": "웹툰 수",
        }.get(x, x),
    )
    fig = px.bar(
        weekday_summary, x="release_weekday_label", y=metric_option,
        title="연재 요일별 다운로드 증가량", text=metric_option,
        hover_data=["webtoon_count", "collect_count"],
    )
    fig.update_xaxes(title="연재 요일", categoryorder="array", categoryarray=WEEKDAY_ORDER)
    fig.update_yaxes(title="값")
    render_plotly(fig, "연재요일별_다운로드_증가량", height=430)

    display = weekday_summary.rename(columns={
        "release_weekday_label": "연재요일", "total_increase": "증가량 합계", "avg_increase": "평균 증가량",
        "median_increase": "중앙값 증가량", "webtoon_count": "웹툰 수", "collect_count": "수집 건수",
    })
    styled_table(display[["연재요일", "웹툰 수", "수집 건수", "증가량 합계", "평균 증가량", "중앙값 증가량"]])

# ════════════════════════════════════════════════════════════
# TOP 랭킹
# ════════════════════════════════════════════════════════════

with tab_rank:
    st.subheader("TOP 랭킹")
    top_n = st.slider("랭킹 개수", 10, 100, 30, step=10, key="ranking_top_n")
    rank_all = ranking_by_title(filtered_df, metric_col)

    rank_metric = st.selectbox(
        "정렬 기준",
        ["total_increase", "avg_increase", "max_increase", "avg_increase_rate", "latest_download_count"],
        format_func=lambda x: {
            "total_increase": "기간 내 증가량 합계",
            "avg_increase": "평균 증가량",
            "max_increase": "최대 증가량",
            "avg_increase_rate": "평균 증가율",
            "latest_download_count": "최신 누적 다운로드",
        }.get(x, x),
    )
    rank_view = rank_all.sort_values(rank_metric, ascending=False).head(top_n)
    fig = px.bar(
        rank_view.sort_values(rank_metric),
        x=rank_metric, y="webtoon_title", orientation="h",
        title=f"웹툰별 TOP {top_n} — {rank_metric}",
        hover_data=["latest_download_count", "total_increase", "avg_increase", "release_weekday_label"],
    )
    fig.update_xaxes(title="값")
    fig.update_yaxes(title="웹툰")
    render_plotly(fig, f"TOP_{top_n}_{rank_metric}", height=max(480, top_n * 22 + 160))

    st.markdown("#### 성장형 / 정체형 보기")
    scatter_df = rank_all.dropna(subset=["latest_download_count", "total_increase"]).copy()
    if len(scatter_df) >= 2:
        fig = px.scatter(
            scatter_df,
            x="latest_download_count", y="total_increase", hover_name="webtoon_title",
            size="collect_count", color="release_weekday_label",
            title="누적 다운로드 규모 vs 기간 내 증가량",
            hover_data=["avg_increase", "avg_increase_rate"],
        )
        fig.update_xaxes(title="최신 누적 다운로드")
        fig.update_yaxes(title="기간 내 다운로드 증가량")
        render_plotly(fig, "누적다운로드_vs_증가량", height=520)

    display = rank_view.rename(columns={
        "webtoon_title": "웹툰명", "release_weekday_label": "연재요일", "total_increase": "증가량 합계",
        "avg_increase": "평균 증가량", "median_increase": "중앙값 증가량", "max_increase": "최대 증가량",
        "avg_increase_rate": "평균 증가율", "latest_download_count": "최신 누적 다운로드",
        "snapshot_count": "스냅샷 수", "collect_count": "수집 건수", "latest_collected_at": "최신 수집시각",
        "series_url": "시리즈 URL",
    })
    show_cols = ["웹툰명", "연재요일", "증가량 합계", "평균 증가량", "최대 증가량", "평균 증가율", "최신 누적 다운로드", "수집 건수", "최신 수집시각", "시리즈 URL"]
    styled_table(display[[c for c in show_cols if c in display.columns]])

# ════════════════════════════════════════════════════════════
# 검수 데이터
# ════════════════════════════════════════════════════════════

with tab_review:
    st.subheader("검수 데이터")
    issue_mask = (
        ~all_df["download_status"].str.lower().eq("ok")
        | ~all_df["match_status"].str.lower().eq("matched")
        | all_df["series_download_count"].isna()
        | all_df["webtoon_title"].eq("")
    )
    issues = all_df[issue_mask].copy()
    c1, c2, c3 = st.columns(3)
    c1.metric("검수 필요 행", f"{len(issues):,}")
    c2.metric("매칭 실패/검토", f"{(~all_df['match_status'].str.lower().eq('matched')).sum():,}")
    c3.metric("다운로드 실패", f"{(~all_df['download_status'].str.lower().eq('ok')).sum():,}")

    status_filter = st.multiselect(
        "다운로드 상태 필터",
        sorted(issues["download_status"].dropna().unique().tolist()),
        default=sorted(issues["download_status"].dropna().unique().tolist()),
    )
    if status_filter:
        issues = issues[issues["download_status"].isin(status_filter)]

    match_filter = st.multiselect(
        "매칭 상태 필터",
        sorted(issues["match_status"].dropna().unique().tolist()),
        default=sorted(issues["match_status"].dropna().unique().tolist()),
    )
    if match_filter:
        issues = issues[issues["match_status"].isin(match_filter)]

    show_cols = [
        "collected_at", "weekday", "release_weekday_label", "webtoon_title", "webtoon_author",
        "series_title", "match_status", "download_status", "series_result_text", "series_url",
        "series_download_text", "series_download_count", "snapshot_id",
    ]
    display = issues[[c for c in show_cols if c in issues.columns]].rename(columns={
        "collected_at": "수집시각", "weekday": "목록요일", "release_weekday_label": "연재요일",
        "webtoon_title": "웹툰명", "webtoon_author": "웹툰 작가", "series_title": "시리즈명",
        "match_status": "매칭상태", "download_status": "다운로드상태", "series_result_text": "검색결과 텍스트",
        "series_url": "시리즈 URL", "series_download_text": "다운로드 텍스트", "series_download_count": "다운로드수",
        "snapshot_id": "스냅샷 ID",
    })
    styled_table(display.head(1000))
    st.download_button(
        "검수 데이터 CSV 다운로드",
        data=display.to_csv(index=False, encoding="utf-8-sig"),
        file_name="download_review_needed.csv",
        mime="text/csv",
    )
