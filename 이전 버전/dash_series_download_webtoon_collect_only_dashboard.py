# -*- coding: utf-8 -*-
"""
네이버 시리즈 다운로드 수 웹툰별 수집시점 대시보드

필요 파일
- series_download_increase_report_history.csv

실행
- streamlit run series_download_webtoon_collect_only_dashboard.py

기능
- 웹툰 1개를 선택해 수집 시점별 누적 다운로드수와 이전 수집 대비 증가수를 한 차트에서 확인합니다.
- y축 2개를 사용합니다.
  - 왼쪽 y축: 누적 다운로드수
  - 오른쪽 y축: 증가수
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


st.set_page_config(page_title="웹툰별 다운로드 수 추이", layout="wide")

BASE_DIR = Path(__file__).parent
DATA_CANDIDATES = [
    BASE_DIR / "series_download_increase_report_history.csv",
    *sorted(BASE_DIR.glob("*download*increase*history*.csv")),
    *sorted(BASE_DIR.glob("series_download*.csv")),
]


def first_existing_path() -> Optional[Path]:
    seen = set()
    for path in DATA_CANDIDATES:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.exists() and path.is_file():
            return path
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


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def standardize_download_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()

    required = [
        "webtoon_title",
        "series_title",
        "series_download_count",
        "download_increase",
        "download_increase_rate",
        "collected_at_kst",
        "dt",
        "snapshot_id",
        "download_status",
        "match_status",
        "release_weekday_ko",
        "weekday",
        "series_url",
        "series_product_no",
        "baseline_download_count",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    df["title"] = df["webtoon_title"].fillna(df["series_title"]).astype(str).str.strip()
    empty_title = df["title"].isin(["", "nan", "None"])
    df.loc[empty_title, "title"] = df.loc[empty_title, "series_title"].astype(str).str.strip()

    df["collected_dt"] = pd.to_datetime(df["collected_at_kst"], errors="coerce")
    df["collected_dt"] = df["collected_dt"].fillna(pd.to_datetime(df["dt"], errors="coerce"))

    numeric_cols = [
        "series_download_count",
        "download_increase",
        "download_increase_rate",
        "baseline_download_count",
    ]
    for col in numeric_cols:
        df[col] = to_number(df[col])

    df = df[df["title"].notna() & (df["title"].astype(str).str.strip() != "")].copy()
    df = df[df["collected_dt"].notna()].copy()
    df = df[df["series_download_count"].notna()].copy()

    # 차트에는 정상 수집/정상 매칭 데이터만 사용합니다.
    if "download_status" in df.columns:
        ok_mask = df["download_status"].isna() | df["download_status"].astype(str).str.lower().eq("ok")
        df = df[ok_mask].copy()
    if "match_status" in df.columns:
        matched_mask = df["match_status"].isna() | df["match_status"].astype(str).str.lower().eq("matched")
        df = df[matched_mask].copy()

    # 같은 작품/같은 수집시각 중복은 마지막 값을 사용합니다.
    sort_cols = ["title", "collected_dt"]
    if "snapshot_id" in df.columns:
        sort_cols.append("snapshot_id")
    df = df.sort_values(sort_cols).drop_duplicates(subset=["title", "collected_dt"], keep="last")

    return df.reset_index(drop=True)


def make_one_title_collect_df(df: pd.DataFrame, title: str) -> pd.DataFrame:
    one = df[df["title"] == title].copy().sort_values("collected_dt")
    if one.empty:
        return one

    one["collect_increase"] = one["series_download_count"].diff()

    # 첫 번째 수집시점은 직전 값이 없으므로 수집기가 계산한 download_increase를 보조값으로 사용합니다.
    first_idx = one.index[0]
    fallback = one.loc[first_idx, "download_increase"]
    one.loc[first_idx, "collect_increase"] = fallback if pd.notna(fallback) else 0

    # 다운로드 누적값이 비정상적으로 감소한 경우 차트 증가수는 0으로 처리합니다.
    one["collect_increase"] = one["collect_increase"].clip(lower=0)
    one["collect_label"] = one["collected_dt"].dt.strftime("%Y-%m-%d %H:%M")
    return one.reset_index(drop=True)


def render_download_chart(chart_df: pd.DataFrame, title: str) -> None:
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Scatter(
            x=chart_df["collect_label"],
            y=chart_df["series_download_count"],
            mode="lines+markers",
            name="누적 다운로드수",
            hovertemplate="수집시각=%{x}<br>누적 다운로드수=%{y:,.0f}<extra></extra>",
        ),
        secondary_y=False,
    )

    fig.add_trace(
        go.Scatter(
            x=chart_df["collect_label"],
            y=chart_df["collect_increase"],
            mode="lines+markers",
            name="이전 수집 대비 증가수",
            line=dict(dash="dash"),
            hovertemplate="수집시각=%{x}<br>증가수=%{y:,.0f}<extra></extra>",
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=f"{title} — 수집 시점별 누적 다운로드수 / 증가수",
        height=600,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
        margin=dict(t=90, b=90, l=80, r=90),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="수집 시각", showgrid=True, gridcolor="rgba(128,128,128,0.18)", tickangle=-35),
    )
    fig.update_yaxes(
        title_text="누적 다운로드수",
        secondary_y=False,
        showgrid=True,
        gridcolor="rgba(128,128,128,0.18)",
        tickformat=",",
    )
    fig.update_yaxes(
        title_text="증가수",
        secondary_y=True,
        showgrid=False,
        tickformat=",",
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": True, "scrollZoom": True},
    )


def clean_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", str(name))
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name or "webtoon_download"


# ─────────────────────────────────────────────────────────────
# 화면
# ─────────────────────────────────────────────────────────────

st.title("웹툰별 다운로드 수 추이")
st.caption("웹툰 1개를 선택해 수집 시점별 누적 다운로드수와 이전 수집 대비 증가수를 함께 확인합니다. Plotly 범례 클릭으로 각 선을 켜고 끌 수 있습니다.")

path = first_existing_path()
if path is not None:
    raw_df = load_data_from_path(str(path))
else:
    st.warning("같은 폴더에서 `series_download_increase_report_history.csv` 파일을 찾지 못했음. 아래에서 직접 업로드하면 됨.")
    uploaded = st.file_uploader("다운로드 수 히스토리 CSV 업로드", type=["csv"])
    if uploaded is None:
        st.stop()
    raw_df = load_data_from_upload(uploaded)

valid_df = standardize_download_data(raw_df)
if valid_df.empty:
    st.error("분석 가능한 다운로드 수 데이터가 없음. `series_download_count`, `collected_at_kst`, `webtoon_title` 컬럼을 확인해야 함.")
    st.stop()

titles = sorted(valid_df["title"].dropna().astype(str).unique().tolist())
selected_title = st.selectbox("웹툰 선택", titles, index=0)

chart_df = make_one_title_collect_df(valid_df, selected_title)
if chart_df.empty:
    st.info("선택한 웹툰의 수집 시점별 데이터를 만들 수 없음.")
    st.stop()

render_download_chart(chart_df, selected_title)

latest = chart_df.iloc[-1]
prev = chart_df.iloc[-2] if len(chart_df) >= 2 else None

m1, m2, m3 = st.columns(3)
m1.metric("최근 누적 다운로드수", f"{latest['series_download_count']:,.0f}")
m2.metric("최근 수집 증가수", f"{latest['collect_increase']:,.0f}")
m3.metric("수집 횟수", f"{len(chart_df):,}")

show_cols = [
    "collect_label",
    "series_download_count",
    "collect_increase",
    "download_increase",
    "download_increase_rate",
    "release_weekday_ko",
    "snapshot_id",
]
show_cols = [col for col in show_cols if col in chart_df.columns]
out = chart_df[show_cols].copy()
out = out.rename(
    columns={
        "collect_label": "수집시각",
        "series_download_count": "누적 다운로드수",
        "collect_increase": "이전 수집 대비 증가수",
        "download_increase": "리포트 증가량",
        "download_increase_rate": "리포트 증가율",
        "release_weekday_ko": "연재요일",
        "snapshot_id": "스냅샷ID",
    }
)

st.markdown("#### 수집 시점별 데이터")
st.dataframe(out, use_container_width=True, hide_index=True)

st.download_button(
    "수집 시점별 데이터 CSV 다운로드",
    out.to_csv(index=False, encoding="utf-8-sig"),
    file_name=f"{clean_filename(selected_title)}_수집시점별_다운로드추이.csv",
    mime="text/csv",
)
