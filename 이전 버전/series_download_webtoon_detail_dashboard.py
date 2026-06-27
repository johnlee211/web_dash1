# -*- coding: utf-8 -*-
"""
네이버 시리즈 다운로드 수 웹툰별 상세 대시보드

필요 파일
- series_download_increase_report_history.csv

실행
- streamlit run series_download_webtoon_detail_dashboard.py

기능
- 웹툰 1개를 선택해 주별 누적 다운로드수와 주간 증가수를 한 차트에서 확인합니다.
- 왼쪽 사이드바를 사용하지 않습니다.
- Plotly 범례를 클릭해 원하는 선만 켜고 끌 수 있습니다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st


st.set_page_config(page_title="웹툰별 다운로드 추이", layout="wide")

BASE_DIR = Path(__file__).parent
DEFAULT_CANDIDATES = [
    BASE_DIR / "series_download_increase_report_history.csv",
    *sorted(BASE_DIR.glob("*download*increase*history*.csv")),
    *sorted(BASE_DIR.glob("series_download*.csv")),
]


def _num(x):
    return pd.to_numeric(x, errors="coerce")


def _first_existing_path() -> Optional[Path]:
    seen = set()
    for p in DEFAULT_CANDIDATES:
        if p in seen:
            continue
        seen.add(p)
        if p.exists() and p.is_file():
            return p
    return None


def read_csv_safely(path_or_file) -> pd.DataFrame:
    for enc in ["utf-8-sig", "utf-8", "cp949"]:
        try:
            return pd.read_csv(path_or_file, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path_or_file)


@st.cache_data(show_spinner="다운로드 수 데이터를 읽는 중입니다...")
def load_data_from_path(path_str: str) -> pd.DataFrame:
    return read_csv_safely(path_str)


@st.cache_data(show_spinner="업로드 파일을 읽는 중입니다...")
def load_data_from_upload(uploaded_file) -> pd.DataFrame:
    return read_csv_safely(uploaded_file)


def standardize_download_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()

    # 필수 컬럼 보완
    for col in [
        "webtoon_title", "series_title", "series_download_count", "download_increase",
        "download_increase_rate", "collected_at_kst", "dt", "snapshot_id",
        "download_status", "match_status", "release_weekday_ko", "weekday",
        "series_url", "series_product_no", "baseline_download_count",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    df["title"] = df["webtoon_title"].fillna(df["series_title"]).astype(str).str.strip()
    df.loc[df["title"].isin(["", "nan", "None"]), "title"] = df["series_title"].astype(str).str.strip()

    # 수집 시각
    df["collected_dt"] = pd.to_datetime(df["collected_at_kst"], errors="coerce")
    df["collected_dt"] = df["collected_dt"].fillna(pd.to_datetime(df["dt"], errors="coerce"))

    # 숫자 컬럼
    for col in ["series_download_count", "download_increase", "download_increase_rate", "baseline_download_count"]:
        df[col] = _num(df[col])

    # 유효 행만 사용
    df = df[df["title"].notna() & (df["title"].astype(str).str.strip() != "")].copy()
    df = df[df["collected_dt"].notna()].copy()
    df = df[df["series_download_count"].notna()].copy()

    # 같은 시점/작품 중복은 마지막 값 유지
    sort_cols = ["title", "collected_dt"]
    if "snapshot_id" in df.columns:
        sort_cols.append("snapshot_id")
    df = df.sort_values(sort_cols).drop_duplicates(
        subset=["title", "collected_dt"], keep="last"
    )

    return df.reset_index(drop=True)


def make_weekly_one_title(df: pd.DataFrame, title: str) -> pd.DataFrame:
    one = df[df["title"] == title].copy()
    if one.empty:
        return pd.DataFrame()

    one = one.sort_values("collected_dt")
    one["week_start"] = one["collected_dt"].dt.to_period("W-MON").apply(lambda p: p.start_time)

    # 한 주 안에 여러 번 수집된 경우, 누적 다운로드수는 그 주의 마지막 수집값을 사용합니다.
    # 증가수는 '주 마지막 누적 다운로드수 - 직전 주 마지막 누적 다운로드수'로 계산합니다.
    weekly = (
        one.groupby("week_start", as_index=False)
        .agg(
            latest_download_count=("series_download_count", "last"),
            week_report_increase_sum=("download_increase", "sum"),
            avg_report_increase_rate=("download_increase_rate", "mean"),
            collect_count=("collected_dt", "count"),
            first_collected_at=("collected_dt", "min"),
            last_collected_at=("collected_dt", "max"),
            release_weekday_ko=("release_weekday_ko", "first"),
            weekday=("weekday", "first"),
            series_url=("series_url", "first"),
            series_product_no=("series_product_no", "first"),
        )
        .sort_values("week_start")
        .reset_index(drop=True)
    )

    weekly["weekly_download_increase"] = weekly["latest_download_count"].diff()

    # 첫 주는 직전 주 기준이 없으므로, 수집기가 만든 증가량 합계가 있으면 보조값으로 사용합니다.
    if len(weekly) > 0:
        first_idx = weekly.index[0]
        fallback = weekly.loc[first_idx, "week_report_increase_sum"]
        weekly.loc[first_idx, "weekly_download_increase"] = fallback if pd.notna(fallback) else 0

    weekly["weekly_download_increase"] = weekly["weekly_download_increase"].clip(lower=0)
    weekly["week_label"] = weekly["week_start"].dt.strftime("%Y-%m-%d")
    weekly["last_collected_label"] = weekly["last_collected_at"].dt.strftime("%Y-%m-%d %H:%M")
    weekly["first_collected_label"] = weekly["first_collected_at"].dt.strftime("%Y-%m-%d %H:%M")
    return weekly


def make_collect_one_title(df: pd.DataFrame, title: str) -> pd.DataFrame:
    one = df[df["title"] == title].copy().sort_values("collected_dt")
    if one.empty:
        return one
    one["collect_increase"] = one["series_download_count"].diff()
    if len(one) > 0:
        first = one.index[0]
        fallback = one.loc[first, "download_increase"]
        one.loc[first, "collect_increase"] = fallback if pd.notna(fallback) else 0
    one["collect_increase"] = one["collect_increase"].clip(lower=0)
    one["collect_label"] = one["collected_dt"].dt.strftime("%Y-%m-%d %H:%M")
    return one


def add_dual_axis_chart(x, cumulative, increase, title: str, x_title: str):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cumulative,
            mode="lines+markers",
            name="누적 다운로드수",
            hovertemplate="%{x}<br>누적 다운로드수=%{y:,.0f}<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=increase,
            mode="lines+markers",
            name="증가수",
            line=dict(dash="dash"),
            hovertemplate="%{x}<br>증가수=%{y:,.0f}<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        title=title,
        height=560,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
        margin=dict(t=90, b=70, l=70, r=80),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title=x_title, showgrid=True, gridcolor="rgba(128,128,128,0.18)"),
    )
    fig.update_yaxes(title_text="누적 다운로드수", secondary_y=False, showgrid=True, gridcolor="rgba(128,128,128,0.18)")
    fig.update_yaxes(title_text="증가수", secondary_y=True, showgrid=False)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True, "scrollZoom": True})


def clean_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", str(name))
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name or "webtoon_download"


# ─────────────────────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────────────────────

st.title("웹툰별 다운로드 수 추이")
st.caption("웹툰 1개를 선택해 주별 누적 다운로드수와 증가수를 함께 확인합니다. Plotly 범례를 클릭하면 원하는 선만 켜고 끌 수 있습니다.")

path = _first_existing_path()
if path is not None:
    raw_df = load_data_from_path(str(path))
else:
    st.warning("같은 폴더에서 `series_download_increase_report_history.csv` 파일을 찾지 못했음. 아래에서 직접 업로드하면 됨.")
    uploaded = st.file_uploader("다운로드 수 히스토리 CSV 업로드", type=["csv"])
    if uploaded is None:
        st.stop()
    raw_df = load_data_from_upload(uploaded)

df_all = standardize_download_data(raw_df)
if df_all.empty:
    st.error("분석 가능한 다운로드 수 데이터가 없음. `series_download_count`, `collected_at_kst`, `webtoon_title` 컬럼을 확인해야 함.")
    st.stop()

# 기본적으로 성공 데이터 우선 사용. 실패/검수 데이터는 차트에 섞이지 않게 제거합니다.
df_valid = df_all.copy()
if "download_status" in df_valid.columns:
    ok_mask = df_valid["download_status"].isna() | df_valid["download_status"].astype(str).str.lower().eq("ok")
    df_valid = df_valid[ok_mask].copy()
if "match_status" in df_valid.columns:
    matched_mask = df_valid["match_status"].isna() | df_valid["match_status"].astype(str).str.lower().eq("matched")
    df_valid = df_valid[matched_mask].copy()

if df_valid.empty:
    st.error("성공적으로 매칭/수집된 데이터가 없음.")
    st.stop()

# ─────────────────────────────────────────────────────────────
# 메인 컨트롤
# ─────────────────────────────────────────────────────────────

titles = sorted(df_valid["title"].dropna().astype(str).unique().tolist())

c1, c2 = st.columns([2.3, 1])
with c1:
    selected_title = st.selectbox("웹툰 선택", titles, index=0)
with c2:
    view_mode = st.radio("표시 단위", ["주별", "수집 시점별"], horizontal=True, index=0)

one_collect = make_collect_one_title(df_valid, selected_title)
one_weekly = make_weekly_one_title(df_valid, selected_title)

if view_mode == "주별":
    chart_df = one_weekly
    if chart_df.empty:
        st.info("선택한 웹툰의 주별 데이터를 만들 수 없음.")
        st.stop()
    add_dual_axis_chart(
        chart_df["week_label"],
        chart_df["latest_download_count"],
        chart_df["weekly_download_increase"],
        f"{selected_title} — 주별 누적 다운로드수 / 증가수",
        "주 시작일",
    )

    latest = chart_df.iloc[-1]
    m1, m2, m3 = st.columns(3)
    m1.metric("최근 누적 다운로드수", f"{latest['latest_download_count']:,.0f}")
    m2.metric("최근 주 증가수", f"{latest['weekly_download_increase']:,.0f}")
    m3.metric("수집 주차 수", f"{len(chart_df):,}")

    show_cols = [
        "week_label", "latest_download_count", "weekly_download_increase",
        "week_report_increase_sum", "collect_count", "first_collected_label", "last_collected_label",
    ]
    out = chart_df[show_cols].copy()
    out.columns = ["주 시작일", "주 마지막 누적 다운로드수", "주간 증가수", "리포트 증가량 합계", "수집 횟수", "첫 수집시각", "마지막 수집시각"]
    st.markdown("#### 주별 데이터")
    st.dataframe(out, use_container_width=True, hide_index=True)
    st.download_button(
        "주별 데이터 CSV 다운로드",
        out.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"{clean_filename(selected_title)}_주별_다운로드추이.csv",
        mime="text/csv",
    )

else:
    chart_df = one_collect
    if chart_df.empty:
        st.info("선택한 웹툰의 수집 시점별 데이터를 만들 수 없음.")
        st.stop()
    add_dual_axis_chart(
        chart_df["collect_label"],
        chart_df["series_download_count"],
        chart_df["collect_increase"],
        f"{selected_title} — 수집 시점별 누적 다운로드수 / 증가수",
        "수집 시각",
    )

    latest = chart_df.iloc[-1]
    m1, m2, m3 = st.columns(3)
    m1.metric("최근 누적 다운로드수", f"{latest['series_download_count']:,.0f}")
    m2.metric("최근 수집 증가수", f"{latest['collect_increase']:,.0f}")
    m3.metric("수집 횟수", f"{len(chart_df):,}")

    show_cols = [
        "collect_label", "series_download_count", "collect_increase",
        "download_increase", "download_increase_rate", "release_weekday_ko", "snapshot_id",
    ]
    show_cols = [c for c in show_cols if c in chart_df.columns]
    out = chart_df[show_cols].copy()
    rename_map = {
        "collect_label": "수집시각",
        "series_download_count": "누적 다운로드수",
        "collect_increase": "이전 수집 대비 증가수",
        "download_increase": "리포트 증가량",
        "download_increase_rate": "리포트 증가율",
        "release_weekday_ko": "연재요일",
        "snapshot_id": "스냅샷ID",
    }
    out = out.rename(columns=rename_map)
    st.markdown("#### 수집 시점별 데이터")
    st.dataframe(out, use_container_width=True, hide_index=True)
    st.download_button(
        "수집 시점별 데이터 CSV 다운로드",
        out.to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"{clean_filename(selected_title)}_수집시점별_다운로드추이.csv",
        mime="text/csv",
    )
