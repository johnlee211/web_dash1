"""
웹툰 팬덤의존도 대시보드

실행:
    streamlit run fandom_dependency_dashboard_clean.py

같은 폴더 또는 하위 data/dashboard_data 폴더에 댓글 파일을 둡니다.
자동 감지가 안 되면 사이드바에서 업로드하면 됩니다.

계산 방식 기본값:
- 기준 공개시각 = uploaded_at_dt 날짜 + 23시
- after_3 = 기준 공개시각 + 3시간
- after_72 = 기준 공개시각 + 72시간
- 사전호응률 = 유료결제 댓글 수 / after_72 이전 댓글 수
- 초기호응률 = 무료/일반 댓글 중 after_3 이전 댓글 수 / after_72 이전 댓글 수
- 최종호응률 = 무료/일반 댓글 중 after_3 이전 댓글 수 / 전체 댓글 수
- 팬덤의존도 = 사전호응률 - 초기호응률
- 결제언급률 = after_72 이전 결제 키워드 포함 댓글 수 / after_72 이전 댓글 수

색상:
- 팬덤의존도 상위 25%: 빨강
- 팬덤의존도 하위 25%: 파랑
- 중간 50%: 회색
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# =========================================================
# 기본 설정
# =========================================================

st.set_page_config(page_title="웹툰 팬덤의존도 대시보드", layout="wide")

BASE_DIR = Path(__file__).parent
SEARCH_DIRS = [
    BASE_DIR,
    BASE_DIR / "data",
    BASE_DIR / "dashboard_data",
    BASE_DIR / "naver_webtoon_one_title_final_v4",
    BASE_DIR / "naver_webtoon_4titles_comments",
]

PAYMENT_KEYWORDS_DEFAULT = "쿠키, 결제, 결재, 구웠, 굽는다, 질렀, 미리보기, 다음화, 유료"

# =========================================================
# 파일 로드
# =========================================================

def read_table(file_or_path, sheet_name=None) -> pd.DataFrame:
    name = getattr(file_or_path, "name", str(file_or_path))
    suffix = Path(name).suffix.lower()

    if suffix == ".csv":
        last_error = None
        for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
            try:
                return pd.read_csv(file_or_path, encoding=enc)
            except Exception as e:
                last_error = e
        raise last_error

    if suffix in [".xlsx", ".xls"]:
        if sheet_name is not None:
            return pd.read_excel(file_or_path, sheet_name=sheet_name)
        return pd.read_excel(file_or_path)

    raise ValueError(f"지원하지 않는 파일 형식입니다: {name}")


def find_files(kind: str) -> List[Path]:
    patterns = {
        "comments": ["*댓글*.csv", "*댓글*.xlsx", "*comment*.csv", "*comment*.xlsx"],
        "episodes": ["*회차정보*.csv", "*회차정보*.xlsx", "*episode*.csv", "*episode*.xlsx"],
    }[kind]

    found: List[Path] = []
    seen = set()
    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        for pat in patterns:
            for p in d.rglob(pat):
                if p.name.startswith("~$"):
                    continue
                key = p.resolve()
                if key not in seen:
                    found.append(p)
                    seen.add(key)
    return sorted(found)


def infer_title_from_filename(path_or_name) -> str:
    name = Path(getattr(path_or_name, "name", str(path_or_name))).stem
    name = re.sub(r"_?전체댓글.*$", "", name)
    name = re.sub(r"_?댓글.*$", "", name)
    name = re.sub(r"_?회차정보.*$", "", name)
    name = re.sub(r"_?회차리스트.*$", "", name)
    name = re.sub(r"_?comments?.*$", "", name, flags=re.I)
    name = re.sub(r"_?episodes?.*$", "", name, flags=re.I)
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def parse_number(x):
    if pd.isna(x):
        return np.nan
    if isinstance(x, (int, float, np.integer, np.floating)):
        return x
    text = str(x).replace(",", "")
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    return float(nums[-1]) if nums else np.nan


def clean_bool(x) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip().lower()
    return s in {"true", "1", "t", "y", "yes", "유료", "유료결제 댓글", "paid"}


def safe_divide(num, den) -> np.ndarray:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return np.where(den > 0, num / den, np.nan)

# =========================================================
# 표준화
# =========================================================

def standardize_comments(df: pd.DataFrame, source_name: str = "") -> pd.DataFrame:
    df = df.copy()
    title_from_file = infer_title_from_filename(source_name)

    rename_map = {
        "웹툰 ID": "title_id", "웹툰ID": "title_id", "titleId": "title_id",
        "웹툰명": "title_name", "웹툰 명": "title_name", "작품명": "title_name",
        "회차": "episode_no", "회차 번호": "episode_no", "no": "episode_no",
        "회차 제목": "episode_title", "제목": "episode_title",
        "공개일": "uploaded_at", "업로드일": "uploaded_at", "업로드 날짜": "uploaded_at",
        "실제작성시각": "actual_written_at", "실제 작성 시각": "actual_written_at",
        "작성시각": "written_at", "작성 시각": "written_at", "작성일": "written_at",
        "작성자": "author", "내용": "content", "댓글내용": "content", "댓글 내용": "content",
        "유료여부": "is_preview_paid_comment", "유료 댓글 여부": "is_preview_paid_comment",
        "베스트": "is_best", "베스트여부": "is_best",
        "좋아요": "like_count", "댓글좋아요": "like_count",
        "답글": "reply_count", "대댓글": "reply_count",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = [
        "title_id", "title_name", "episode_no", "episode_title", "uploaded_at", "uploaded_at_dt",
        "free_release_at", "actual_written_at", "written_at", "author", "content",
        "preview_comment_type", "is_preview_paid_comment", "is_best", "like_count", "reply_count",
        "comment_source", "raw_text",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    df["title_name"] = df["title_name"].fillna("").astype(str).str.strip()
    df.loc[df["title_name"].isin(["", "nan", "None"]), "title_name"] = title_from_file
    df["title_name"] = df["title_name"].replace("", title_from_file)

    df["title_id"] = pd.to_numeric(df["title_id"], errors="coerce").astype("Int64")
    df["episode_no"] = pd.to_numeric(df["episode_no"], errors="coerce")
    df = df.dropna(subset=["episode_no"]).copy()
    df["episode_no"] = df["episode_no"].astype(int)

    for col in ["uploaded_at_dt", "free_release_at", "actual_written_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")

    if df["actual_written_at"].isna().all() and "written_at" in df.columns:
        df["actual_written_at"] = pd.to_datetime(df["written_at"], errors="coerce", format="mixed")

    df["preview_comment_type"] = df["preview_comment_type"].fillna("").astype(str).str.strip()
    df["is_preview_paid_comment"] = df["is_preview_paid_comment"].map(clean_bool)
    df["is_best"] = df["is_best"].map(clean_bool)
    df["content"] = df["content"].fillna("").astype(str)
    df["author"] = df["author"].fillna("").astype(str).str.strip()
    df["episode_title"] = df["episode_title"].fillna("").astype(str)
    df["like_count"] = pd.to_numeric(df["like_count"], errors="coerce")
    df["reply_count"] = pd.to_numeric(df["reply_count"], errors="coerce")

    return df[required].copy()


def standardize_episodes(df: pd.DataFrame, source_name: str = "") -> pd.DataFrame:
    df = df.copy()
    title_from_file = infer_title_from_filename(source_name)

    rename_map = {
        "웹툰 ID": "title_id", "웹툰ID": "title_id", "titleId": "title_id",
        "웹툰명": "title_name", "웹툰 명": "title_name", "작품명": "title_name",
        "회차": "episode_no", "회차 번호": "episode_no", "no": "episode_no",
        "회차 제목": "episode_title", "제목": "episode_title",
        "별점": "rating", "별점 참여 수": "rating_count", "별점참여수": "rating_count",
        "좋아요": "episode_like_count", "회차 좋아요": "episode_like_count",
        "댓글": "platform_comment_count", "댓글수": "platform_comment_count",
        "공개일": "uploaded_at", "업로드일": "uploaded_at", "업로드 날짜": "uploaded_at",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = [
        "title_id", "title_name", "episode_no", "episode_title", "uploaded_at", "uploaded_at_dt",
        "free_release_at", "rating", "rating_count", "episode_like_count", "platform_comment_count", "episode_url",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    df["title_name"] = df["title_name"].fillna("").astype(str).str.strip()
    df.loc[df["title_name"].isin(["", "nan", "None"]), "title_name"] = title_from_file
    df["title_name"] = df["title_name"].replace("", title_from_file)
    df["title_id"] = pd.to_numeric(df["title_id"], errors="coerce").astype("Int64")
    df["episode_no"] = pd.to_numeric(df["episode_no"], errors="coerce")
    df = df.dropna(subset=["episode_no"]).copy()
    df["episode_no"] = df["episode_no"].astype(int)

    for col in ["uploaded_at_dt", "free_release_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
    for col in ["rating", "rating_count", "episode_like_count", "platform_comment_count"]:
        df[col] = df[col].apply(parse_number)

    return df[required].copy()


@st.cache_data(show_spinner="데이터 파일을 읽는 중입니다...")
def load_data(comment_uploads=None, episode_uploads=None):
    comment_frames = []
    episode_frames = []
    auto_comment_files = find_files("comments")
    auto_episode_files = find_files("episodes")

    for p in auto_comment_files:
        try:
            comment_frames.append(standardize_comments(read_table(p), str(p)))
        except Exception as e:
            # 자동 감지 파일 중 형식이 다른 파일은 조용히 제외
            pass

    for p in auto_episode_files:
        try:
            episode_frames.append(standardize_episodes(read_table(p), str(p)))
        except Exception:
            pass

    if comment_uploads:
        for up in comment_uploads:
            try:
                comment_frames.append(standardize_comments(read_table(up), up.name))
            except Exception as e:
                st.warning(f"댓글 파일을 읽지 못했습니다: {up.name} / {e}")

    if episode_uploads:
        for up in episode_uploads:
            try:
                episode_frames.append(standardize_episodes(read_table(up), up.name))
            except Exception as e:
                st.warning(f"회차정보 파일을 읽지 못했습니다: {up.name} / {e}")

    comments = pd.concat(comment_frames, ignore_index=True, sort=False) if comment_frames else pd.DataFrame()
    episodes = pd.concat(episode_frames, ignore_index=True, sort=False) if episode_frames else pd.DataFrame()

    if not episodes.empty:
        episodes = episodes.sort_values(["title_name", "episode_no"]).drop_duplicates(
            subset=["title_id", "title_name", "episode_no"], keep="last"
        )

    return comments, episodes, {
        "comment_files": [str(p.name) for p in auto_comment_files],
        "episode_files": [str(p.name) for p in auto_episode_files],
    }

# =========================================================
# 메타 / 필터
# =========================================================

def make_title_label(row: pd.Series) -> str:
    title = str(row.get("title_name", "")).strip()
    tid = row.get("title_id")
    if title and title.lower() != "nan":
        return title
    return str(tid) if not pd.isna(tid) else "제목 없음"


def get_title_options(comments: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for df in [comments, episodes]:
        if not df.empty:
            parts.append(df[["title_id", "title_name"]].drop_duplicates())
    if not parts:
        return pd.DataFrame(columns=["title_id", "title_name", "display_name"])
    out = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["title_id", "title_name"])
    out["display_name"] = out.apply(make_title_label, axis=1)
    return out.sort_values("display_name").reset_index(drop=True)


def filter_title(df: pd.DataFrame, meta: pd.DataFrame, display_name: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    row = meta[meta["display_name"] == display_name]
    if row.empty:
        return df[df["title_name"].astype(str) == display_name].copy()
    r = row.iloc[0]
    mask = df["title_name"].astype(str).eq(str(r["title_name"]))
    if "title_id" in df.columns and not pd.isna(r["title_id"]):
        mask = mask | df["title_id"].eq(r["title_id"])
    return df[mask].copy()

# =========================================================
# 팬덤의존도 지표 계산
# =========================================================

def compile_keywords(keyword_text: str) -> List[str]:
    # 쉼표/슬래시/줄바꿈/파이프 모두 허용
    parts = re.split(r"[,\n/|]+", keyword_text)
    return [p.strip() for p in parts if p.strip()]


def contains_keyword(series: pd.Series, keywords: List[str]) -> pd.Series:
    if not keywords:
        return pd.Series(False, index=series.index)
    pattern = "|".join(re.escape(k) for k in keywords)
    return series.fillna("").astype(str).str.contains(pattern, case=False, regex=True, na=False)


def classify_comment_flags(c: pd.DataFrame) -> pd.DataFrame:
    out = c.copy()
    ptype = out["preview_comment_type"].fillna("").astype(str).str.strip()

    # 기준 기준의 핵심 라벨: 유료결제 댓글 / 무료공개 이후 댓글.
    # 현재 수집 파일에서는 무료 공개 이후 댓글이 '일반 댓글'로 들어오는 경우가 있어 둘 다 무료/일반으로 처리합니다.
    out["is_paid_notebook"] = ptype.eq("유료결제 댓글") | ptype.str.contains("유료결제", na=False) | out["is_preview_paid_comment"].fillna(False).astype(bool)
    out["is_free_after_notebook"] = (~out["is_paid_notebook"]) & (
        ptype.eq("무료공개 이후 댓글")
        | ptype.eq("일반 댓글")
        | ptype.str.contains("무료|일반", na=False)
        | ptype.eq("")
    )
    return out


def build_reference_time(c: pd.DataFrame, mode: str, reference_hour: int, backup_hour: int) -> pd.Series:
    uploaded = pd.to_datetime(c.get("uploaded_at_dt", pd.NaT), errors="coerce", format="mixed")
    free_release = pd.to_datetime(c.get("free_release_at", pd.NaT), errors="coerce", format="mixed")

    if mode == "uploaded_at_dt 날짜 + 기준시각":
        # 기준의 uploaded_at_dt + 23h를 일반화. 날짜 컬럼이 00시일 때 정확히 같은 결과입니다.
        ref = uploaded.dt.normalize() + pd.to_timedelta(reference_hour, unit="h")
        fallback = free_release.fillna(uploaded)
        return ref.fillna(fallback)

    # 검산용: 수집기에 들어있는 free_release_at을 직접 기준으로 쓰는 방식
    fallback = uploaded.dt.normalize() + pd.to_timedelta(backup_hour, unit="h")
    return free_release.fillna(fallback)


def calculate_fandom_metrics(
    comments: pd.DataFrame,
    episodes: pd.DataFrame,
    reference_mode: str,
    reference_hour: int,
    backup_hour: int,
    initial_hours: float,
    base_hours: float,
    keyword_text: str,
) -> pd.DataFrame:
    if comments.empty:
        return pd.DataFrame()

    c = comments.copy()
    c = classify_comment_flags(c)
    c["actual_written_at"] = pd.to_datetime(c["actual_written_at"], errors="coerce", format="mixed")
    c["reference_at"] = build_reference_time(c, reference_mode, reference_hour, backup_hour)
    c["after_initial"] = c["reference_at"] + pd.to_timedelta(initial_hours, unit="h")
    c["after_base"] = c["reference_at"] + pd.to_timedelta(base_hours, unit="h")
    c["elapsed_hours"] = (c["actual_written_at"] - c["reference_at"]).dt.total_seconds() / 3600

    # 하한선 없이 <= after_3 / <= after_72 기준으로 계산합니다.
    c["in_base_window_notebook"] = c["actual_written_at"].notna() & c["after_base"].notna() & (c["actual_written_at"] <= c["after_base"])
    c["in_initial_window_notebook"] = (
        c["is_free_after_notebook"]
        & c["actual_written_at"].notna()
        & c["after_initial"].notna()
        & (c["actual_written_at"] <= c["after_initial"])
    )

    keywords = compile_keywords(keyword_text)
    c["has_payment_keyword"] = contains_keyword(c["content"], keywords)
    c["payment_keyword_in_base"] = c["in_base_window_notebook"] & c["has_payment_keyword"]

    group_cols = ["title_id", "title_name", "episode_no"]

    total = c.groupby(group_cols, dropna=False).size().rename("total_comments")
    paid = c[c["is_paid_notebook"]].groupby(group_cols, dropna=False).size().rename("paid_comments")
    base = c[c["in_base_window_notebook"]].groupby(group_cols, dropna=False).size().rename("base_72h_comments")
    initial = c[c["in_initial_window_notebook"]].groupby(group_cols, dropna=False).size().rename("initial_comments")
    pay_kw = c[c["payment_keyword_in_base"]].groupby(group_cols, dropna=False).size().rename("payment_keyword_72h_comments")

    meta = c.groupby(group_cols, dropna=False).agg(
        episode_title=("episode_title", "first"),
        reference_at=("reference_at", "first"),
        after_initial=("after_initial", "first"),
        after_base=("after_base", "first"),
        best_comments=("is_best", "sum"),
        unique_authors=("author", lambda x: x.replace("", np.nan).nunique()),
        comment_like_sum=("like_count", "sum"),
    )

    summary = meta.join([total, paid, base, initial, pay_kw], how="left").reset_index()
    count_cols = ["total_comments", "paid_comments", "base_72h_comments", "initial_comments", "payment_keyword_72h_comments"]
    for col in count_cols:
        summary[col] = pd.to_numeric(summary[col], errors="coerce").fillna(0).astype(int)

    summary["pre_response_rate"] = safe_divide(summary["paid_comments"], summary["base_72h_comments"])
    summary["initial_response_rate"] = safe_divide(summary["initial_comments"], summary["base_72h_comments"])
    summary["final_response_rate"] = safe_divide(summary["initial_comments"], summary["total_comments"])
    summary["fandom_dependency"] = summary["pre_response_rate"] - summary["initial_response_rate"]
    summary["payment_mention_rate"] = safe_divide(summary["payment_keyword_72h_comments"], summary["base_72h_comments"])

    if not episodes.empty:
        ep = episodes.copy()
        ep_cols = [
            "title_id", "title_name", "episode_no", "episode_title", "rating", "rating_count",
            "episode_like_count", "platform_comment_count", "episode_url",
        ]
        ep = ep[[col for col in ep_cols if col in ep.columns]].drop_duplicates(
            subset=["title_id", "title_name", "episode_no"], keep="last"
        )
        summary = summary.merge(
            ep,
            on=["title_id", "title_name", "episode_no"],
            how="left",
            suffixes=("", "_episode"),
        )
        if "episode_title_episode" in summary.columns:
            summary["episode_title"] = summary["episode_title"].replace("", np.nan).fillna(summary["episode_title_episode"])
            summary = summary.drop(columns=["episode_title_episode"])

    return summary.sort_values(["title_name", "episode_no"]).reset_index(drop=True)

# =========================================================
# 차트 / 표시
# =========================================================

def metric_card(label: str, value, fmt: str = "{:.3f}", help_text: Optional[str] = None):
    if pd.isna(value):
        st.metric(label, "-", help=help_text)
    elif isinstance(value, (int, np.integer)):
        st.metric(label, f"{value:,}", help=help_text)
    elif isinstance(value, float):
        st.metric(label, fmt.format(value), help=help_text)
    else:
        st.metric(label, str(value), help=help_text)


def classify_dependency_colors(values: pd.Series, red_top_pct: int, blue_bottom_pct: int) -> Tuple[List[str], float, float]:
    s = pd.to_numeric(values, errors="coerce")
    valid = s.dropna()
    if valid.empty:
        return ["#D3D3D3"] * len(s), np.nan, np.nan

    red_q = max(0.0, min(1.0, 1 - red_top_pct / 100))
    blue_q = max(0.0, min(1.0, blue_bottom_pct / 100))
    red_cutoff = float(valid.quantile(red_q))
    blue_cutoff = float(valid.quantile(blue_q))

    colors = []
    for x in s:
        if pd.isna(x):
            colors.append("#D3D3D3")
        elif x >= red_cutoff:
            colors.append("#D62728")
        elif x <= blue_cutoff:
            colors.append("#1F77B4")
        else:
            colors.append("#D3D3D3")
    return colors, red_cutoff, blue_cutoff


def plot_fandom_dependency(df: pd.DataFrame, title: str, red_top_pct: int, blue_bottom_pct: int, chart_height: int):
    data = df.sort_values("episode_no").copy()
    colors, red_cutoff, blue_cutoff = classify_dependency_colors(data["fandom_dependency"], red_top_pct, blue_bottom_pct)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=data["episode_no"],
        y=data["fandom_dependency"],
        marker_color=colors,
        customdata=np.stack([
            data["paid_comments"],
            data["initial_comments"],
            data["base_72h_comments"],
            data["total_comments"],
            data["pre_response_rate"],
            data["initial_response_rate"],
            data["final_response_rate"],
            data["payment_mention_rate"],
        ], axis=-1),
        hovertemplate=(
            "%{x}화<br>"
            "팬덤의존도=%{y:.3f}<br>"
            "유료결제 댓글=%{customdata[0]:,.0f}<br>"
            "초기 무료/일반 댓글=%{customdata[1]:,.0f}<br>"
            "72시간 기준 댓글=%{customdata[2]:,.0f}<br>"
            "전체 댓글=%{customdata[3]:,.0f}<br>"
            "사전호응률=%{customdata[4]:.3f}<br>"
            "초기호응률=%{customdata[5]:.3f}<br>"
            "최종호응률=%{customdata[6]:.3f}<br>"
            "결제언급률=%{customdata[7]:.3f}"
            "<extra></extra>"
        ),
    ))
    fig.add_hline(y=0, line_width=1, line_color="#333333")
    fig.update_layout(
        title=f"{title} 회차별 팬덤의존도",
        xaxis_title="회차",
        yaxis_title="팬덤의존도",
        height=chart_height,
        margin=dict(t=65, b=45, l=60, r=30),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.12)")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"색상 기준: 하위 {blue_bottom_pct}% ≤ {blue_cutoff:.3f} 파랑, "
        f"상위 {red_top_pct}% ≥ {red_cutoff:.3f} 빨강, 나머지 회색."
    )


def plot_response_rates(df: pd.DataFrame, title: str):
    data = df.sort_values("episode_no")
    fig = go.Figure()
    for col, label, dash in [
        ("pre_response_rate", "사전호응률", "solid"),
        ("initial_response_rate", "초기호응률", "dash"),
        ("final_response_rate", "최종호응률", "dot"),
        ("payment_mention_rate", "결제언급률", "longdash"),
    ]:
        fig.add_trace(go.Scatter(
            x=data["episode_no"],
            y=data[col],
            mode="lines+markers",
            name=label,
            line=dict(width=2.3, dash=dash),
            hovertemplate="회차=%{x}<br>값=%{y:.3f}<extra>%{fullData.name}</extra>",
        ))
    fig.add_hline(y=1, line_width=1, line_dash="dot", line_color="rgba(0,0,0,0.35)")
    fig.update_layout(
        title=f"{title} 회차별 호응률 지표",
        xaxis_title="회차",
        yaxis_title="지표값",
        height=460,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=80, b=50, l=60, r=30),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
    )
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.12)")
    st.plotly_chart(fig, use_container_width=True)


def plot_counts(df: pd.DataFrame, title: str):
    data = df.sort_values("episode_no")
    fig = go.Figure()
    for col, label in [
        ("paid_comments", "유료결제 댓글"),
        ("initial_comments", "초기 무료/일반 댓글"),
        ("base_72h_comments", "72시간 기준 댓글"),
        ("total_comments", "전체 댓글"),
    ]:
        fig.add_trace(go.Scatter(
            x=data["episode_no"], y=data[col], mode="lines+markers", name=label,
            hovertemplate="회차=%{x}<br>댓글=%{y:,.0f}<extra>%{fullData.name}</extra>",
        ))
    fig.update_layout(
        title=f"{title} 회차별 분모/분자 확인",
        xaxis_title="회차",
        yaxis_title="댓글 수",
        height=460,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=80, b=50, l=60, r=30),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)


def make_download_button(df: pd.DataFrame, filename: str):
    st.download_button(
        "CSV 다운로드",
        data=df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=filename,
        mime="text/csv",
    )

# =========================================================
# 앱
# =========================================================

st.title("웹툰 팬덤의존도 대시보드")
st.caption("사전호응률, 초기호응률, 최종호응률, 팬덤의존도, 결제언급률을 회차별로 계산합니다.")

with st.expander("파일 업로드, 자동 감지가 안 될 때만 사용", expanded=False):
    comment_uploads = st.file_uploader("댓글 파일", type=["csv", "xlsx", "xls"], accept_multiple_files=True)
    episode_uploads = st.file_uploader("회차정보 파일", type=["csv", "xlsx", "xls"], accept_multiple_files=True)

comments_all, episodes_all, file_info = load_data(comment_uploads, episode_uploads)

if comments_all.empty:
    st.info("댓글 데이터를 찾지 못했습니다. 이 파일과 같은 폴더에 댓글 CSV/XLSX를 두거나 위 업로드 영역에서 파일을 올려주세요.")
    st.stop()

# 계산 기준은 고정값으로 사용합니다. 화면에는 노출하지 않습니다.
reference_mode = "uploaded_at_dt 날짜 + 기준시각"
reference_hour = 23
backup_hour = 22
initial_hours = 3.0
base_hours = 72.0
keyword_text = PAYMENT_KEYWORDS_DEFAULT
red_top_pct = 25
blue_bottom_pct = 25
chart_height = 430

with st.expander("자동 감지된 파일", expanded=False):
    st.write("댓글 파일")
    st.code("\n".join(file_info["comment_files"]) if file_info["comment_files"] else "없음")
    st.write("회차정보 파일")
    st.code("\n".join(file_info["episode_files"]) if file_info["episode_files"] else "없음")

meta = get_title_options(comments_all, episodes_all)
title_options = meta["display_name"].tolist()
title_sel = st.selectbox("웹툰 선택", title_options)

comments = filter_title(comments_all, meta, title_sel)
episodes = filter_title(episodes_all, meta, title_sel)
summary = calculate_fandom_metrics(
    comments=comments,
    episodes=episodes,
    reference_mode=reference_mode,
    reference_hour=reference_hour,
    backup_hour=backup_hour,
    initial_hours=initial_hours,
    base_hours=base_hours,
    keyword_text=keyword_text,
)

if summary.empty:
    st.warning("선택한 웹툰의 계산 가능한 데이터가 없습니다.")
    st.stop()

all_eps = sorted(summary["episode_no"].dropna().astype(int).unique())
ep_range = st.slider("회차 범위", min_value=min(all_eps), max_value=max(all_eps), value=(min(all_eps), max(all_eps)))
view = summary[(summary["episode_no"] >= ep_range[0]) & (summary["episode_no"] <= ep_range[1])].copy()

avg = view[["pre_response_rate", "initial_response_rate", "final_response_rate", "fandom_dependency", "payment_mention_rate"]].mean(numeric_only=True)

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    metric_card("분석 회차", int(view["episode_no"].nunique()))
with c2:
    metric_card("전체 댓글", int(view["total_comments"].sum()))
with c3:
    metric_card("유료 댓글", int(view["paid_comments"].sum()))
with c4:
    metric_card("72시간 기준 댓글", int(view["base_72h_comments"].sum()))
with c5:
    metric_card("평균 팬덤의존도", float(avg.get("fandom_dependency", np.nan)))
with c6:
    metric_card("평균 결제언급률", float(avg.get("payment_mention_rate", np.nan)))

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(["팬덤의존도", "호응률 지표", "분모/분자 확인", "상세 데이터"])

with tab1:
    st.markdown("#### 회차별 팬덤의존도")
    st.caption("빨강 = 팬덤의존도 상위 회차, 파랑 = 초기 무료/일반 유입이 상대적으로 강한 회차, 회색 = 중간 구간입니다.")
    plot_fandom_dependency(view, title_sel, red_top_pct, blue_bottom_pct, chart_height)

    left, right = st.columns(2)
    with left:
        st.markdown("##### 팬덤의존도 높은 회차 TOP 5")
        cols = ["episode_no", "episode_title", "fandom_dependency", "pre_response_rate", "initial_response_rate", "paid_comments", "base_72h_comments"]
        st.dataframe(view.sort_values("fandom_dependency", ascending=False)[cols].head(5), use_container_width=True, hide_index=True)
    with right:
        st.markdown("##### 초기 유입이 강한 회차 TOP 5")
        cols = ["episode_no", "episode_title", "fandom_dependency", "pre_response_rate", "initial_response_rate", "initial_comments", "base_72h_comments"]
        st.dataframe(view.sort_values("fandom_dependency", ascending=True)[cols].head(5), use_container_width=True, hide_index=True)

with tab2:
    st.markdown("#### 사전호응률 / 초기호응률 / 최종호응률 / 결제언급률")
    plot_response_rates(view, title_sel)

with tab3:
    st.markdown("#### 계산에 사용된 댓글 수")
    plot_counts(view, title_sel)
    st.info("72시간 기준 댓글은 actual_written_at <= after_72로 계산합니다. 따라서 유료결제 댓글도 72시간 기준 분모에 포함될 수 있습니다.")

with tab4:
    st.markdown("#### 회차별 계산 결과")
    display_cols = [
        "episode_no", "episode_title", "reference_at", "after_initial", "after_base",
        "paid_comments", "initial_comments", "base_72h_comments", "total_comments",
        "pre_response_rate", "initial_response_rate", "final_response_rate", "fandom_dependency",
        "payment_keyword_72h_comments", "payment_mention_rate",
        "rating", "rating_count", "episode_like_count", "best_comments", "unique_authors",
    ]
    display_cols = [c for c in display_cols if c in view.columns]
    st.dataframe(view[display_cols], use_container_width=True, hide_index=True)
    make_download_button(view[display_cols], f"{title_sel}_팬덤의존도.csv")

    st.markdown("#### 특정 회차 댓글 확인")
    ep_sel = st.selectbox("회차 선택", all_eps, index=len(all_eps) - 1)
    raw = comments.copy()
    raw = classify_comment_flags(raw)
    raw["actual_written_at"] = pd.to_datetime(raw["actual_written_at"], errors="coerce", format="mixed")
    raw["reference_at"] = build_reference_time(raw, reference_mode, reference_hour, backup_hour)
    raw["after_initial"] = raw["reference_at"] + pd.to_timedelta(initial_hours, unit="h")
    raw["after_base"] = raw["reference_at"] + pd.to_timedelta(base_hours, unit="h")
    raw["elapsed_hours"] = (raw["actual_written_at"] - raw["reference_at"]).dt.total_seconds() / 3600
    raw["has_payment_keyword"] = contains_keyword(raw["content"], compile_keywords(keyword_text))
    raw_ep = raw[raw["episode_no"] == ep_sel].copy()

    filter_kind = st.radio(
        "댓글 구간",
        ["전체", "유료결제 댓글", f"초기 무료/일반 {initial_hours:g}시간", f"72시간 기준 {base_hours:g}시간", "결제 키워드 포함"],
        horizontal=True,
    )
    if filter_kind == "유료결제 댓글":
        raw_ep = raw_ep[raw_ep["is_paid_notebook"]]
    elif filter_kind == f"초기 무료/일반 {initial_hours:g}시간":
        raw_ep = raw_ep[raw_ep["is_free_after_notebook"] & (raw_ep["actual_written_at"] <= raw_ep["after_initial"])]
    elif filter_kind == f"72시간 기준 {base_hours:g}시간":
        raw_ep = raw_ep[raw_ep["actual_written_at"] <= raw_ep["after_base"]]
    elif filter_kind == "결제 키워드 포함":
        raw_ep = raw_ep[raw_ep["has_payment_keyword"]]

    raw_ep = raw_ep.sort_values("actual_written_at")
    raw_cols = [
        "episode_no", "episode_title", "author", "content", "written_at", "actual_written_at", "elapsed_hours",
        "preview_comment_type", "is_paid_notebook", "is_free_after_notebook", "is_best", "like_count", "reply_count",
    ]
    raw_cols = [c for c in raw_cols if c in raw_ep.columns]
    st.dataframe(raw_ep[raw_cols].head(1000), use_container_width=True, hide_index=True)
