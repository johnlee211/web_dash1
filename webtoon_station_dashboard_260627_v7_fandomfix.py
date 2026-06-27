"""
웹툰 반응 비교 대시보드 v5 FIXED

목적
- 수집한 댓글 파일과 회차정보 파일을 자동으로 읽어 단일 작품 모니터링, 두 작품 비교를 수행합니다.
- 사용자가 직접 웹툰 1개 또는 2개를 선택합니다.
- 장르/태그, 공개 회차 수로 비교 후보를 좁힐 수 있습니다.
- 전체, 초기 20%, 중간 60%, 최근 20%, 사용자 지정 회차 구간을 모두 지원합니다.

권장 폴더 구조
- 이 파일과 데이터 파일을 같은 폴더에 둡니다.
- 또는 하위 폴더에 둬도 됩니다. dashboard_data, naver_webtoon_* 폴더까지 자동 탐색합니다.
- 댓글 파일명에는 가급적 '댓글' 또는 'comments'가 포함되게 합니다.
- 회차정보 파일명에는 가급적 '회차정보', 'episode', 'episodes'가 포함되게 합니다.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ════════════════════════════════════════════════════════════
# 기본 설정
# ════════════════════════════════════════════════════════════

st.set_page_config(page_title="웹툰스테이션 통합 대시보드", layout="wide")

BASE_DIR = Path(__file__).parent
SEARCH_DIRS = [
    BASE_DIR,
    BASE_DIR / "dashboard_data",
    BASE_DIR / "naver_webtoon_4titles_comments",
    BASE_DIR / "naver_webtoon_one_title_final_v4",
]

CHART_H = 410

DEFAULT_KNOWN_WEBTOONS = {
    839353: "한계 찢는 천재마법사",
    838569: "해피 페이스",
    827323: "남주의 정석",
    829495: "아임 파인 땡큐, 앤유?",
    822657: "환생천마",
    822862: "사천당가의 검신급 소가주가 되었다",
}

# 예시 추천 쌍일 뿐, 대시보드에서는 사용자가 직접 두 작품을 선택합니다.
EXAMPLE_PAIRS = [
    ("한계 찢는 천재마법사", "해피 페이스", "50화대 액션/판타지 예시"),
    ("남주의 정석", "아임 파인 땡큐, 앤유?", "80화대 로맨스 예시"),
    ("환생천마", "사천당가의 검신급 소가주가 되었다", "110화대 무협/판무 예시"),
]

# Plotly HTML 다운로드 키 초기화
st.session_state["plotly_download_button_counter"] = 0


# ════════════════════════════════════════════════════════════
# 공통 유틸
# ════════════════════════════════════════════════════════════

def safe_str(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def safe_numeric(s, default=np.nan):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def parse_count_text(x):
    """댓글수, 좋아요수처럼 쉼표가 섞인 숫자 텍스트를 숫자로 변환합니다."""
    if pd.isna(x):
        return np.nan
    text = str(x).strip()
    if text == "":
        return np.nan
    text = text.replace(",", "")
    nums = re.findall(r"-?\d+", text)
    if not nums:
        return np.nan
    try:
        return int(nums[-1])
    except Exception:
        return np.nan


def parse_rating_text(x):
    if pd.isna(x):
        return np.nan
    text = str(x).strip().replace("점", "").replace(",", ".")
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return float(nums[0]) if nums else np.nan




def make_unique_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Streamlit/pyarrow 출력 오류를 막기 위해 중복 컬럼명을 유니크하게 만듭니다."""
    out = df.copy()
    seen = {}
    new_cols = []
    for col in out.columns:
        col_str = str(col)
        if col_str not in seen:
            seen[col_str] = 0
            new_cols.append(col_str)
        else:
            seen[col_str] += 1
            new_cols.append(f"{col_str}_{seen[col_str]}")
    out.columns = new_cols
    return out


def safe_dataframe(df: pd.DataFrame, **kwargs):
    """중복 컬럼명이 있어도 st.dataframe이 터지지 않도록 안전하게 출력합니다."""
    if df is None:
        st.info("표시할 데이터가 없습니다.")
        return
    out = make_unique_columns(pd.DataFrame(df))
    return st.dataframe(out, **kwargs)


def normalize_title_name(name: str) -> str:
    name = str(name or "").strip()
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name


def infer_title_name_from_path(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"_?전체댓글.*$", "", stem)
    stem = re.sub(r"_?댓글.*$", "", stem)
    stem = re.sub(r"_?회차정보.*$", "", stem)
    stem = re.sub(r"_?유료.*$", "", stem)
    stem = re.sub(r"_?comments.*$", "", stem, flags=re.I)
    stem = re.sub(r"_?episodes.*$", "", stem, flags=re.I)
    return normalize_title_name(stem)


def clean_bool_series(s) -> pd.Series:
    if isinstance(s, pd.Series):
        return s.map(lambda x: str(x).strip().lower() in ["true", "1", "t", "y", "yes", "유료", "유료결제 댓글"] if not pd.isna(x) else False)
    return pd.Series(dtype=bool)


def _safe_html_filename(name: str) -> str:
    name = str(name or "plotly_chart").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.replace("—", "_").replace("–", "_")
    name = name.strip("._ ")
    return (name[:90] or "plotly_chart") + ".html"


def _get_fig_title(fig) -> str:
    try:
        return fig.layout.title.text or "plotly_chart"
    except Exception:
        return "plotly_chart"


def _make_plotly_html(fig):
    html_mode = st.session_state.get("plotly_html_download_mode", "가벼운 파일(CDN)")
    include_js = "cdn" if html_mode == "가벼운 파일(CDN)" else True
    return fig.to_html(
        include_plotlyjs=include_js,
        full_html=True,
        config={"displayModeBar": True, "responsive": True, "scrollZoom": True},
    )


def render_plotly_chart(fig, *args, download_label="📥 이 차트 HTML 다운로드", download_key=None, **kwargs):
    st.plotly_chart(fig, *args, **kwargs)
    st.session_state["plotly_download_button_counter"] = st.session_state.get("plotly_download_button_counter", 0) + 1
    title = _get_fig_title(fig)
    if download_key is None:
        download_key = f"plotly_html_{st.session_state['plotly_download_button_counter']}_" + _safe_html_filename(title).replace(".html", "")
    try:
        st.download_button(
            label=download_label,
            data=_make_plotly_html(fig),
            file_name=_safe_html_filename(title),
            mime="text/html",
            key=download_key,
        )
    except Exception as e:
        st.warning(f"차트 HTML 다운로드 버튼을 만들지 못했습니다: {e}")


def base_layout(fig, title, y_title="값", x_title="회차", height=CHART_H):
    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        xaxis=dict(title=x_title, showgrid=True, gridcolor="rgba(0,0,0,0.07)"),
        yaxis=dict(title=y_title, showgrid=True, gridcolor="rgba(0,0,0,0.07)"),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
        height=height,
        margin=dict(t=90, b=50, l=60, r=45),
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def add_line(fig, df, x, y, name, dash="solid", width=2.3, secondary_y=False):
    if df.empty or y not in df.columns:
        return
    fig.add_trace(
        go.Scatter(
            x=df[x],
            y=df[y],
            mode="lines+markers",
            name=name,
            line=dict(width=width, dash=dash),
            marker=dict(size=5),
            hovertemplate="회차=%{x}<br>값=%{y:,.2f}<extra>%{fullData.name}</extra>",
        ),
        secondary_y=secondary_y,
    )


# ════════════════════════════════════════════════════════════
# 파일 탐색 및 로드
# ════════════════════════════════════════════════════════════

def iter_candidate_files() -> List[Path]:
    paths = []
    for d in SEARCH_DIRS:
        if not d.exists():
            continue
        for ext in ["*.csv", "*.xlsx", "*.xls"]:
            paths.extend(d.rglob(ext))
    # 중복 제거
    unique = []
    seen = set()
    for p in paths:
        if p.resolve() not in seen:
            unique.append(p)
            seen.add(p.resolve())
    return sorted(unique)


def classify_file(path: Path) -> str:
    name = path.name.lower()
    if any(k in name for k in ["회차정보", "episode", "episodes"]):
        return "episode"
    if any(k in name for k in ["댓글", "comment", "comments"]):
        return "comment"
    if any(k in name for k in ["순위", "상세", "meta", "웹툰스테이션", "tag", "tags", "태그"]):
        return "meta"
    return "unknown"


def read_table(path_or_file, sheet_name=None) -> pd.DataFrame:
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
        try:
            if sheet_name is not None:
                return pd.read_excel(path_or_file, sheet_name=sheet_name)
            return pd.read_excel(path_or_file)
        except ValueError:
            return pd.read_excel(path_or_file)
    raise ValueError(f"지원하지 않는 파일 형식입니다: {name}")


def standardize_comments(df: pd.DataFrame, source_path: Optional[Path] = None) -> pd.DataFrame:
    df = df.copy()
    title_from_file = infer_title_name_from_path(source_path) if source_path else ""

    rename_map = {
        "웹툰 ID": "title_id", "웹툰ID": "title_id", "titleId": "title_id",
        "웹툰명": "title_name", "웹툰 명": "title_name", "작품명": "title_name",
        "회차 번호": "episode_no", "회차": "episode_no", "no": "episode_no",
        "회차 제목": "episode_title", "제목": "episode_title",
        "작성자": "author", "댓글내용": "content", "댓글 내용": "content", "내용": "content",
        "작성시각": "written_at", "작성 시각": "written_at", "작성일": "written_at",
        "실제작성시각": "actual_written_at", "실제 작성 시각": "actual_written_at",
        "좋아요": "like_count", "댓글좋아요": "like_count", "싫어요": "dislike_count", "대댓글": "reply_count",
        "베스트": "is_best", "베스트여부": "is_best",
        "유료여부": "is_preview_paid_comment", "유료 댓글 여부": "is_preview_paid_comment",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = [
        "title_id", "title_name", "week", "episode_no", "episode_title", "uploaded_at", "uploaded_at_dt",
        "free_release_at", "comment_no", "author", "content", "written_at", "actual_written_at",
        "reply_count", "like_count", "dislike_count", "is_best", "comment_source", "preview_comment_type",
        "is_preview_paid_comment", "raw_text", "collected_at",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    if df["title_name"].isna().all() or (df["title_name"].astype(str).str.strip() == "").all():
        df["title_name"] = title_from_file
    df["title_name"] = df["title_name"].replace("", np.nan).fillna(title_from_file)

    if "title_id" in df.columns:
        df["title_id"] = pd.to_numeric(df["title_id"], errors="coerce").astype("Int64")
        df["title_name"] = df.apply(
            lambda r: DEFAULT_KNOWN_WEBTOONS.get(int(r["title_id"]), r["title_name"]) if not pd.isna(r["title_id"]) else r["title_name"],
            axis=1,
        )

    df["episode_no"] = pd.to_numeric(df["episode_no"], errors="coerce").astype("Int64")
    for dt_col in ["uploaded_at_dt", "free_release_at", "actual_written_at", "collected_at"]:
        df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")

    for col in ["like_count", "dislike_count", "reply_count", "comment_no"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "is_preview_paid_comment" in df.columns:
        df["is_preview_paid_comment"] = clean_bool_series(df["is_preview_paid_comment"])
    else:
        df["is_preview_paid_comment"] = False

    if df["preview_comment_type"].isna().all():
        df["preview_comment_type"] = df["is_preview_paid_comment"].map({True: "유료결제 댓글", False: "일반 댓글"})

    df["is_best"] = clean_bool_series(df["is_best"])
    df["author"] = df["author"].fillna("").astype(str).str.strip()
    df["content"] = df["content"].fillna("").astype(str).str.strip()
    df["raw_text"] = df["raw_text"].fillna("").astype(str)

    # author가 비어 있으면 raw_text 앞부분에서 보완합니다.
    blank_author = df["author"].isin(["", "nan", "None"])
    if blank_author.any():
        extracted = df.loc[blank_author, "raw_text"].str.extract(r"^\s*([^\s]+\([^\)]*\*+[^\)]*\))", expand=False)
        df.loc[blank_author, "author"] = extracted.fillna("")

    if df["actual_written_at"].isna().all() and "written_at" in df.columns:
        df["actual_written_at"] = pd.to_datetime(df["written_at"], errors="coerce")

    df = df.dropna(subset=["episode_no"]).copy()
    df["episode_no"] = df["episode_no"].astype(int)
    return df[required].copy()


def standardize_episodes(df: pd.DataFrame, source_path: Optional[Path] = None) -> pd.DataFrame:
    df = df.copy()
    title_from_file = infer_title_name_from_path(source_path) if source_path else ""

    rename_map = {
        "웹툰 ID": "title_id", "웹툰ID": "title_id", "titleId": "title_id",
        "웹툰명": "title_name", "웹툰 명": "title_name", "작품명": "title_name",
        "회차 번호": "episode_no", "회차": "episode_no", "no": "episode_no",
        "회차 제목": "episode_title", "제목": "episode_title",
        "별점": "rating", "별점 참여 수": "rating_count", "별점참여수": "rating_count",
        "댓글": "platform_comment_count", "댓글수": "platform_comment_count",
        "좋아요": "episode_like_count", "회차 좋아요": "episode_like_count",
        "업로드일": "uploaded_at", "공개일": "uploaded_at", "업로드 날짜": "uploaded_at",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = [
        "title_id", "title_name", "week", "episode_no", "episode_title", "uploaded_at", "uploaded_at_dt",
        "free_release_at", "rating", "rating_count", "episode_like_count", "platform_comment_count", "episode_url", "collected_at",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = np.nan

    if df["title_name"].isna().all() or (df["title_name"].astype(str).str.strip() == "").all():
        df["title_name"] = title_from_file
    df["title_name"] = df["title_name"].replace("", np.nan).fillna(title_from_file)

    df["title_id"] = pd.to_numeric(df["title_id"], errors="coerce").astype("Int64")
    df["title_name"] = df.apply(
        lambda r: DEFAULT_KNOWN_WEBTOONS.get(int(r["title_id"]), r["title_name"]) if not pd.isna(r["title_id"]) else r["title_name"],
        axis=1,
    )

    df["episode_no"] = pd.to_numeric(df["episode_no"], errors="coerce").astype("Int64")
    df["rating"] = df["rating"].apply(parse_rating_text)
    for col in ["rating_count", "episode_like_count", "platform_comment_count"]:
        df[col] = df[col].apply(parse_count_text)

    for dt_col in ["uploaded_at_dt", "free_release_at", "collected_at"]:
        df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")

    df = df.dropna(subset=["episode_no"]).copy()
    df["episode_no"] = df["episode_no"].astype(int)
    return df[required].copy()


def try_load_old_episode_file(path: Path) -> Optional[pd.DataFrame]:
    """기존 한찢마/해피페이스 회차정보 파일처럼 Sheet2에 정보가 있는 경우를 처리합니다."""
    try:
        xls = pd.ExcelFile(path)
    except Exception:
        return None
    sheet_names = xls.sheet_names
    for sheet in ["Sheet2", "웹툰회차데이터", "회차정보", sheet_names[0]]:
        if sheet not in sheet_names:
            continue
        try:
            df = pd.read_excel(path, sheet_name=sheet)
        except Exception:
            continue
        if {"웹툰 ID", "회차 번호"}.issubset(set(df.columns)):
            return standardize_episodes(df, path)
    return None


def load_meta_files(paths: List[Path]) -> pd.DataFrame:
    frames = []
    for p in paths:
        if p.suffix.lower() not in [".xlsx", ".xls", ".csv"]:
            continue
        try:
            if p.suffix.lower() == ".csv":
                df = read_table(p)
                frames.append(df)
            else:
                xls = pd.ExcelFile(p)
                target_sheets = [s for s in xls.sheet_names if any(k in s for k in ["상세", "정보", "meta", "웹툰"])] or xls.sheet_names
                for s in target_sheets:
                    try:
                        df = pd.read_excel(p, sheet_name=s)
                        if any(c in df.columns for c in ["웹툰 ID", "title_id", "웹툰명", "웹툰 명", "해시태그", "장르"]):
                            frames.append(df)
                            break
                    except Exception:
                        continue
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=["title_id", "title_name", "tags", "genre", "episode_count_meta"])
    meta = pd.concat(frames, ignore_index=True, sort=False)
    rename_map = {
        "웹툰 ID": "title_id", "웹툰ID": "title_id", "웹툰명": "title_name", "웹툰 명": "title_name",
        "작품명": "title_name", "해시태그": "tags", "태그": "tags", "장르": "genre", "세부장르": "genre",
        "회차": "episode_count_meta", "총회차": "episode_count_meta",
    }
    meta = meta.rename(columns={c: rename_map.get(c, c) for c in meta.columns})
    for col in ["title_id", "title_name", "tags", "genre", "episode_count_meta"]:
        if col not in meta.columns:
            meta[col] = np.nan
    meta["title_id"] = pd.to_numeric(meta["title_id"], errors="coerce").astype("Int64")
    meta["title_name"] = meta["title_name"].fillna("").astype(str).str.strip()
    meta["tags"] = meta["tags"].fillna("").astype(str)
    meta["genre"] = meta["genre"].fillna("").astype(str)
    meta["episode_count_meta"] = pd.to_numeric(meta["episode_count_meta"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    meta = meta.drop_duplicates(subset=["title_id", "title_name"], keep="first")
    return meta[["title_id", "title_name", "tags", "genre", "episode_count_meta"]]



def deduplicate_comments_keep_best(comments: pd.DataFrame) -> pd.DataFrame:
    """베스트 댓글 영역과 전체댓글 영역에서 같은 댓글이 중복 수집된 경우 1개로 합칩니다.
    - 동일 기준: title_id, title_name, episode_no, author, content, written_at
    - is_best는 하나라도 True면 True로 유지합니다.
    - 좋아요/싫어요/대댓글 수는 더 큰 값을 유지합니다.
    """
    if comments.empty:
        return comments
    df = comments.copy()
    key_cols = [c for c in ["title_id", "title_name", "episode_no", "author", "content", "written_at"] if c in df.columns]
    if not key_cols:
        return df

    for c in key_cols:
        df[c] = df[c].fillna("").astype(str).str.strip() if c not in ["title_id", "episode_no"] else df[c]

    numeric_max_cols = [c for c in ["comment_no", "reply_count", "like_count", "dislike_count"] if c in df.columns]
    bool_cols = [c for c in ["is_best", "is_preview_paid_comment"] if c in df.columns]
    first_cols = [c for c in df.columns if c not in set(key_cols + numeric_max_cols + bool_cols)]

    agg = {c: "first" for c in first_cols}
    for c in numeric_max_cols:
        agg[c] = "max"
    for c in bool_cols:
        agg[c] = "max"

    out = df.groupby(key_cols, dropna=False, as_index=False).agg(agg)

    # groupby 결과에서 key 컬럼과 agg 컬럼 순서가 섞일 수 있으므로 원래 컬럼 순서로 복원합니다.
    for col in df.columns:
        if col not in out.columns:
            out[col] = np.nan
    out = out[df.columns]

    if "comment_source" in out.columns:
        # 동일 댓글이 베스트/전체 양쪽에서 발견된 경우 병합 표시
        dup_keys = df.groupby(key_cols, dropna=False)["comment_source"].nunique().reset_index(name="source_n")
        out = out.merge(dup_keys, on=key_cols, how="left")
        out.loc[out["source_n"].fillna(1) > 1, "comment_source"] = "best+all"
        out = out.drop(columns=["source_n"])

    return out.reset_index(drop=True)

@st.cache_data(show_spinner="데이터 파일을 읽는 중입니다...")
def load_all_data(comment_uploads=None, episode_uploads=None, meta_uploads=None):
    auto_files = iter_candidate_files()
    auto_comment_files = [p for p in auto_files if classify_file(p) == "comment"]
    auto_episode_files = [p for p in auto_files if classify_file(p) == "episode"]
    auto_meta_files = [p for p in auto_files if classify_file(p) == "meta"]

    comment_frames = []
    episode_frames = []

    for p in auto_comment_files:
        try:
            comment_frames.append(standardize_comments(read_table(p), p))
        except Exception:
            pass

    for p in auto_episode_files:
        try:
            if p.suffix.lower() in [".xlsx", ".xls"]:
                old = try_load_old_episode_file(p)
                if old is not None:
                    episode_frames.append(old)
                    continue
            episode_frames.append(standardize_episodes(read_table(p), p))
        except Exception:
            pass

    if comment_uploads:
        for up in comment_uploads:
            try:
                comment_frames.append(standardize_comments(read_table(up), Path(up.name)))
            except Exception as e:
                st.warning(f"댓글 업로드 파일을 읽지 못했습니다: {up.name} / {e}")

    if episode_uploads:
        for up in episode_uploads:
            try:
                episode_frames.append(standardize_episodes(read_table(up), Path(up.name)))
            except Exception as e:
                st.warning(f"회차정보 업로드 파일을 읽지 못했습니다: {up.name} / {e}")

    meta_paths = auto_meta_files
    # 업로드 meta는 캐시 함수 안에서 ExcelFile을 다시 읽기 어려울 수 있으므로 직접 read_table로 처리
    meta_frames = []
    auto_meta = load_meta_files(meta_paths)
    if not auto_meta.empty:
        meta_frames.append(auto_meta)
    if meta_uploads:
        for up in meta_uploads:
            try:
                meta_frames.append(load_meta_files([Path(up.name)]))
            except Exception:
                try:
                    raw = read_table(up)
                    meta_frames.append(raw)
                except Exception:
                    pass

    comments = pd.concat(comment_frames, ignore_index=True, sort=False) if comment_frames else pd.DataFrame()
    episodes = pd.concat(episode_frames, ignore_index=True, sort=False) if episode_frames else pd.DataFrame()
    meta = pd.concat(meta_frames, ignore_index=True, sort=False) if meta_frames else pd.DataFrame(columns=["title_id", "title_name", "tags", "genre", "episode_count_meta"])

    if not comments.empty:
        comments = deduplicate_comments_keep_best(comments)
    if not episodes.empty:
        episodes = episodes.sort_values(["title_name", "episode_no"]).drop_duplicates(
            subset=["title_id", "title_name", "episode_no"], keep="last"
        )
    if not meta.empty:
        meta = meta.drop_duplicates(subset=["title_id", "title_name"], keep="first")

    return comments, episodes, meta, [str(p.relative_to(BASE_DIR)) if p.is_relative_to(BASE_DIR) else str(p) for p in auto_comment_files], [str(p.relative_to(BASE_DIR)) if p.is_relative_to(BASE_DIR) else str(p) for p in auto_episode_files]


# ════════════════════════════════════════════════════════════
# 데이터 가공
# ════════════════════════════════════════════════════════════

def build_webtoon_meta(comments: pd.DataFrame, episodes: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    parts = []
    if not comments.empty:
        parts.append(comments[["title_id", "title_name"]].drop_duplicates())
    if not episodes.empty:
        parts.append(episodes[["title_id", "title_name"]].drop_duplicates())
    if not meta.empty:
        parts.append(meta[["title_id", "title_name"]].drop_duplicates())
    if not parts:
        return pd.DataFrame(columns=["title_id", "title_name", "episode_count", "tags", "genre"])

    base = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["title_id", "title_name"])
    ep_count = pd.DataFrame()
    if not episodes.empty:
        ep_count = episodes.groupby(["title_id", "title_name"], dropna=False)["episode_no"].max().reset_index(name="episode_count")
    elif not comments.empty:
        ep_count = comments.groupby(["title_id", "title_name"], dropna=False)["episode_no"].max().reset_index(name="episode_count")

    out = base.merge(ep_count, on=["title_id", "title_name"], how="left") if not ep_count.empty else base.assign(episode_count=np.nan)
    if not meta.empty:
        m = meta.copy()
        if "episode_count_meta" in m.columns:
            m["episode_count_meta"] = pd.to_numeric(m["episode_count_meta"], errors="coerce")
        # 1차: title_id + title_name으로 결합
        out = out.merge(m[["title_id", "title_name", "tags", "genre", "episode_count_meta"]], on=["title_id", "title_name"], how="left")
        # 2차: 작품명이 약간 다르더라도 title_id가 같으면 태그/장르를 보완
        if "title_id" in out.columns:
            m_by_id = m.dropna(subset=["title_id"]).drop_duplicates("title_id").set_index("title_id")
            for col in ["tags", "genre", "episode_count_meta"]:
                if col in out.columns and col in m_by_id.columns:
                    out[col] = out[col].replace("", np.nan).fillna(out["title_id"].map(m_by_id[col]))
        out["episode_count"] = out["episode_count"].fillna(out.get("episode_count_meta"))
    else:
        out["tags"] = ""
        out["genre"] = ""

    out["episode_count"] = pd.to_numeric(out["episode_count"], errors="coerce")
    out["tags"] = out["tags"].fillna("").astype(str)
    out["genre"] = out["genre"].fillna("").astype(str)
    out["display_name"] = out["title_name"].fillna("").astype(str)
    out.loc[out["display_name"].str.strip() == "", "display_name"] = out["title_id"].astype(str)
    return out.sort_values(["display_name"]).reset_index(drop=True)


def split_tags(tag_text: str) -> List[str]:
    text = str(tag_text or "")
    text = re.sub(r"[#\[\]'\"]", "", text)
    parts = re.split(r"[,/|;\s]+", text)
    return sorted({p.strip() for p in parts if p.strip() and p.strip().lower() != "nan"})


def title_key_from_name(meta_df: pd.DataFrame, name: str):
    row = meta_df[meta_df["display_name"] == name]
    if row.empty:
        return None
    r = row.iloc[0]
    return (r["title_id"], r["title_name"])


def filter_by_title(df: pd.DataFrame, title_name: str, meta_df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    key = title_key_from_name(meta_df, title_name)
    if key is None:
        return df[df["title_name"] == title_name].copy()
    title_id, tname = key
    mask = df["title_name"].astype(str).eq(str(tname))
    if not pd.isna(title_id) and "title_id" in df.columns:
        mask = mask | df["title_id"].eq(title_id)
    return df[mask].copy()


def build_episode_summary(comments: pd.DataFrame, episodes: pd.DataFrame) -> pd.DataFrame:
    ep = episodes.copy() if not episodes.empty else pd.DataFrame()
    if not ep.empty:
        for col in ["rating", "rating_count", "episode_like_count", "platform_comment_count"]:
            if col in ep.columns:
                ep[col] = pd.to_numeric(ep[col], errors="coerce")
    comment_summary = pd.DataFrame()
    if not comments.empty:
        c = comments.copy()
        c["is_preview_paid_comment"] = c["is_preview_paid_comment"].fillna(False).astype(bool)
        c["is_best"] = c["is_best"].fillna(False).astype(bool)
        c["like_count"] = pd.to_numeric(c["like_count"], errors="coerce").fillna(0)
        c["reply_count"] = pd.to_numeric(c["reply_count"], errors="coerce").fillna(0)
        group_cols = ["title_id", "title_name", "episode_no"]
        comment_summary = c.groupby(group_cols, dropna=False).agg(
            scraped=("episode_no", "size"),
            paid=("is_preview_paid_comment", "sum"),
            best_count=("is_best", "sum"),
            best_like_mean=("like_count", lambda x: x[c.loc[x.index, "is_best"]].mean() if c.loc[x.index, "is_best"].any() else 0),
            best_reply_mean=("reply_count", lambda x: x[c.loc[x.index, "is_best"]].mean() if c.loc[x.index, "is_best"].any() else 0),
            unique_authors=("author", lambda x: x.replace("", np.nan).nunique()),
        ).reset_index()
        best_paid = c[c["is_best"]].groupby(group_cols, dropna=False)["is_preview_paid_comment"].sum().reset_index(name="best_paid")
        comment_summary = comment_summary.merge(best_paid, on=group_cols, how="left")
        comment_summary["best_paid"] = comment_summary["best_paid"].fillna(0)
        comment_summary["free"] = comment_summary["scraped"] - comment_summary["paid"]
        comment_summary["best_free"] = comment_summary["best_count"] - comment_summary["best_paid"]

    if ep.empty and not comment_summary.empty:
        merged = comment_summary.copy()
        for col in ["episode_title", "uploaded_at", "uploaded_at_dt", "free_release_at", "rating", "rating_count", "episode_like_count", "platform_comment_count", "episode_url"]:
            merged[col] = np.nan
    elif not ep.empty and comment_summary.empty:
        merged = ep.copy()
        for col in ["scraped", "paid", "free", "best_count", "best_paid", "best_free", "best_like_mean", "best_reply_mean", "unique_authors"]:
            merged[col] = 0
    else:
        merged = ep.merge(comment_summary, on=["title_id", "title_name", "episode_no"], how="outer")

    if merged.empty:
        return merged

    for col in ["scraped", "paid", "free", "best_count", "best_paid", "best_free", "unique_authors"]:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)

    merged["paid_ratio"] = np.where(merged["scraped"] > 0, merged["paid"] / merged["scraped"] * 100, 0)
    merged["best_paid_ratio"] = np.where(merged["best_count"] > 0, merged["best_paid"] / merged["best_count"] * 100, 0)
    rating_count_numeric = pd.to_numeric(merged.get("rating_count", 0), errors="coerce").fillna(0)
    merged["comment_per_rating"] = np.where(
        rating_count_numeric > 0,
        merged["scraped"] / rating_count_numeric * 100,
        0,
    )
    merged["author_per_rating"] = np.where(
        rating_count_numeric > 0,
        pd.to_numeric(merged.get("unique_authors", 0), errors="coerce").fillna(0) / rating_count_numeric * 100,
        0,
    )
    merged = merged.sort_values(["title_name", "episode_no"]).reset_index(drop=True)
    return merged


def get_segment_episodes(episodes: Iterable[int], segment: str, custom_range: Optional[Tuple[int, int]] = None) -> List[int]:
    eps = sorted([int(e) for e in pd.Series(list(episodes)).dropna().unique()])
    if not eps:
        return []
    n = len(eps)
    k = max(1, math.ceil(n * 0.2))
    if segment == "초기 20%":
        return eps[:k]
    if segment == "최근 20%":
        return eps[-k:]
    if segment == "중간 60%":
        if n <= k * 2:
            return eps
        return eps[k:-k]
    if segment == "사용자 지정" and custom_range is not None:
        a, b = custom_range
        return [e for e in eps if a <= e <= b]
    return eps


def filter_summary_segment(summary: pd.DataFrame, segment: str, custom_range: Optional[Tuple[int, int]] = None) -> pd.DataFrame:
    if summary.empty:
        return summary.copy()
    out_parts = []
    for _, g in summary.groupby(["title_id", "title_name"], dropna=False):
        eps = get_segment_episodes(g["episode_no"], segment, custom_range)
        out_parts.append(g[g["episode_no"].isin(eps)].copy())
    return pd.concat(out_parts, ignore_index=True) if out_parts else summary.iloc[0:0].copy()


def filter_comments_segment(comments: pd.DataFrame, summary_segment: pd.DataFrame) -> pd.DataFrame:
    if comments.empty or summary_segment.empty:
        return comments.iloc[0:0].copy()
    keys = summary_segment[["title_id", "title_name", "episode_no"]].drop_duplicates()
    return comments.merge(keys, on=["title_id", "title_name", "episode_no"], how="inner")


def metric_summary(df: pd.DataFrame, comments: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    if df.empty:
        return {k: 0 for k in ["episodes", "scraped", "paid", "free", "paid_ratio", "rating", "rating_count", "episode_like_count", "best_count", "best_paid_ratio"]}
    total_comments = df["scraped"].sum()
    paid = df["paid"].sum()
    best = df["best_count"].sum()
    best_paid = df["best_paid"].sum() if "best_paid" in df.columns else 0
    return {
        "episodes": df["episode_no"].nunique(),
        "scraped": total_comments,
        "paid": paid,
        "free": df["free"].sum(),
        "paid_ratio": paid / total_comments * 100 if total_comments else 0,
        "rating": pd.to_numeric(df.get("rating", np.nan), errors="coerce").mean(),
        "rating_count": pd.to_numeric(df.get("rating_count", np.nan), errors="coerce").mean(),
        "episode_like_count": pd.to_numeric(df.get("episode_like_count", np.nan), errors="coerce").mean(),
        "best_count": best,
        "best_paid_ratio": best_paid / best * 100 if best else 0,
        "unique_authors": comments["author"].replace("", np.nan).nunique() if comments is not None and not comments.empty and "author" in comments.columns else 0,
    }


def scale_series(s: pd.Series, method: str):
    s = pd.to_numeric(s, errors="coerce").astype(float)
    if method == "원본값":
        return s
    if method == "첫 회차=100 지수화":
        valid = s.dropna()
        if valid.empty or valid.iloc[0] == 0:
            return pd.Series([0] * len(s), index=s.index, dtype=float)
        return s / valid.iloc[0] * 100
    mn, mx = s.min(skipna=True), s.max(skipna=True)
    if pd.isna(mn) or pd.isna(mx) or mn == mx:
        return pd.Series([50] * len(s), index=s.index, dtype=float)
    return (s - mn) / (mx - mn) * 100


# ════════════════════════════════════════════════════════════
# 차트 함수
# ════════════════════════════════════════════════════════════

def show_summary_cards(summary_dict: Dict[str, float], prefix=""):
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric(f"{prefix}회차 수", f"{int(summary_dict.get('episodes', 0)):,}")
    c2.metric(f"{prefix}댓글 수", f"{int(summary_dict.get('scraped', 0)):,}")
    c3.metric(f"{prefix}유료 비율", f"{summary_dict.get('paid_ratio', 0):.1f}%")
    c4.metric(f"{prefix}평균 별점", f"{summary_dict.get('rating', 0):.2f}" if not pd.isna(summary_dict.get('rating', np.nan)) else "-")
    c5.metric(f"{prefix}평균 별점 참여", f"{summary_dict.get('rating_count', 0):,.0f}" if not pd.isna(summary_dict.get('rating_count', np.nan)) else "-")
    c6.metric(f"{prefix}베스트 댓글", f"{int(summary_dict.get('best_count', 0)):,}")


def plot_single_episode_metrics(df: pd.DataFrame, title: str):
    st.markdown("#### 회차별 별점, 별점 참여자 수, 좋아요, 전체 댓글")
    if df.empty:
        st.info("표시할 회차 데이터가 없습니다.")
        return
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    add_line(fig, df, "episode_no", "rating_count", "별점 참여자 수", secondary_y=False)
    add_line(fig, df, "episode_no", "episode_like_count", "회차 좋아요 수", dash="dot", secondary_y=False)
    add_line(fig, df, "episode_no", "scraped", "전체 댓글 수", dash="longdash", secondary_y=False)
    if "author_per_rating" in df.columns:
        add_line(fig, df, "episode_no", "author_per_rating", "별점 참여자 중 댓글 작성자 비율(%)", dash="longdashdot", secondary_y=True)
    add_line(fig, df, "episode_no", "rating", "별점", dash="dash", secondary_y=True)
    base_layout(fig, f"{title} 회차별 별점/참여/좋아요/댓글", "참여자 수 / 좋아요 수 / 댓글 수")
    fig.update_yaxes(title_text="별점 / 작성자 비율(%)", secondary_y=True)
    render_plotly_chart(fig, use_container_width=True)

def plot_single_comments(df: pd.DataFrame, title: str):
    st.markdown("#### 회차별 댓글 수, 유료/일반 댓글")
    if df.empty:
        st.info("표시할 댓글 데이터가 없습니다.")
        return
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    add_line(fig, df, "episode_no", "scraped", "전체 댓글", secondary_y=False)
    add_line(fig, df, "episode_no", "paid", "유료 댓글", dash="dash", secondary_y=False)
    add_line(fig, df, "episode_no", "free", "일반 댓글", dash="dot", secondary_y=False)
    add_line(fig, df, "episode_no", "paid_ratio", "유료 비율(%)", dash="longdash", secondary_y=True)
    base_layout(fig, f"{title} 회차별 댓글 반응", "댓글 수")
    fig.update_yaxes(title_text="유료 비율(%)", secondary_y=True)
    render_plotly_chart(fig, use_container_width=True)


def plot_best_comments(df: pd.DataFrame, title: str):
    st.markdown("#### 회차별 베스트 댓글")
    if df.empty:
        st.info("표시할 베스트 댓글 데이터가 없습니다.")
        return
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    add_line(fig, df, "episode_no", "best_count", "베스트 댓글 수", secondary_y=False)
    add_line(fig, df, "episode_no", "best_paid", "유료 베스트 댓글 수", dash="dash", secondary_y=False)
    add_line(fig, df, "episode_no", "best_paid_ratio", "베스트 중 유료 비율(%)", dash="dot", secondary_y=True)
    base_layout(fig, f"{title} 회차별 베스트 댓글 구조", "베스트 댓글 수")
    fig.update_yaxes(title_text="유료 비율(%)", secondary_y=True)
    render_plotly_chart(fig, use_container_width=True)


def plot_compare_lines(df1: pd.DataFrame, df2: pd.DataFrame, n1: str, n2: str, metric: str, label: str, title: str, key_prefix: Optional[str] = None):
    key_prefix = key_prefix or f"cmp_{metric}"
    scale = st.selectbox(
        f"{label} 표시 방식",
        ["원본값", "0~100 정규화", "첫 회차=100 지수화"],
        index=0,
        key=f"{key_prefix}_scale",
        help="두 웹툰의 단위 차이가 너무 클 때 정규화 또는 지수화를 선택하면 등락을 보기 쉽습니다.",
    )
    fig = go.Figure()
    for name, data, dash in [(n1, df1, "solid"), (n2, df2, "dash")]:
        if metric not in data.columns:
            continue
        y = data[metric] if scale == "원본값" else scale_series(data[metric], scale)
        fig.add_trace(go.Scatter(
            x=data["episode_no"], y=y, mode="lines+markers", name=name,
            line=dict(dash=dash),
            customdata=pd.to_numeric(data[metric], errors="coerce"),
            hovertemplate="회차=%{x}<br>표시값=%{y:,.2f}<br>원본값=%{customdata:,.2f}<extra>%{fullData.name}</extra>",
        ))
    y_title = label if scale == "원본값" else f"{label} 변환값"
    base_layout(fig, f"{title} — {scale}", y_title)
    render_plotly_chart(fig, use_container_width=True)

def plot_compare_comments(df1: pd.DataFrame, df2: pd.DataFrame, n1: str, n2: str):
    st.markdown("#### 회차별 댓글 반응 비교")
    metric_options = {
        "전체 댓글 수": "scraped",
        "유료 댓글 수": "paid",
        "일반 댓글 수": "free",
        "유료 댓글 비율(%)": "paid_ratio",
        "베스트 댓글 수": "best_count",
        "베스트 중 유료 비율(%)": "best_paid_ratio",
    }
    selected = st.multiselect("비교할 댓글 지표", list(metric_options.keys()), default=["전체 댓글 수", "유료 댓글 비율(%)"], key="cmp_comment_metrics")
    scale = st.selectbox("스케일", ["원본값", "0~100 정규화", "첫 회차=100 지수화"], index=0, key="cmp_comment_scale")
    if not selected:
        return
    fig = go.Figure()
    for label in selected:
        col = metric_options[label]
        for name, data in [(n1, df1), (n2, df2)]:
            if col not in data.columns:
                continue
            y = data[col] if scale == "원본값" else scale_series(data[col], scale)
            dash = "dash" if name == n2 else "solid"
            fig.add_trace(go.Scatter(x=data["episode_no"], y=y, mode="lines+markers", name=f"{name} - {label}", line=dict(dash=dash)))
    y_title = "값" if scale == "원본값" else "변환값"
    base_layout(fig, f"두 작품 댓글 지표 비교 - {scale}", y_title, height=500)
    render_plotly_chart(fig, use_container_width=True)


def plot_compare_core_metrics(df1: pd.DataFrame, df2: pd.DataFrame, n1: str, n2: str):
    st.markdown("#### 별점, 별점 참여자, 좋아요 비교")
    tab1, tab2, tab3 = st.tabs(["별점", "별점 참여자 수", "회차 좋아요 수"])
    with tab1:
        plot_compare_lines(df1, df2, n1, n2, "rating", "별점", "회차별 별점 비교", key_prefix="cmp_rating")
    with tab2:
        plot_compare_lines(df1, df2, n1, n2, "rating_count", "별점 참여자 수", "회차별 별점 참여자 수 비교", key_prefix="cmp_rating_count")
    with tab3:
        plot_compare_lines(df1, df2, n1, n2, "episode_like_count", "좋아요 수", "회차별 좋아요 수 비교", key_prefix="cmp_like")


def plot_segment_bar(summary_rows: pd.DataFrame):
    if summary_rows.empty:
        return
    metrics = {
        "회차당 평균 댓글": "avg_comments",
        "회차당 평균 유료 댓글": "avg_paid",
        "유료 댓글 비율(%)": "paid_ratio",
        "평균 별점": "avg_rating",
        "평균 별점 참여자 수": "avg_rating_count",
        "평균 좋아요 수": "avg_like",
        "회차당 평균 베스트 댓글": "avg_best",
    }
    selected = st.selectbox("구간 요약 차트 지표", list(metrics.keys()), index=0)
    col = metrics[selected]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=summary_rows["작품"], y=summary_rows[col], name=selected))
    base_layout(fig, f"구간 요약 비교 - {selected}", selected, x_title="작품", height=360)
    render_plotly_chart(fig, use_container_width=True)


def prepare_segment_summary_rows(items: List[Tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    rows = []
    for name, df in items:
        if df.empty:
            continue
        episodes = max(1, df["episode_no"].nunique())
        total = df["scraped"].sum()
        paid = df["paid"].sum()
        rows.append({
            "작품": name,
            "회차 수": episodes,
            "댓글 합계": total,
            "유료 댓글 합계": paid,
            "일반 댓글 합계": df["free"].sum(),
            "유료 댓글 비율(%)": paid / total * 100 if total else 0,
            "avg_comments": total / episodes,
            "avg_paid": paid / episodes,
            "paid_ratio": paid / total * 100 if total else 0,
            "avg_rating": pd.to_numeric(df.get("rating", np.nan), errors="coerce").mean(),
            "avg_rating_count": pd.to_numeric(df.get("rating_count", np.nan), errors="coerce").mean(),
            "avg_like": pd.to_numeric(df.get("episode_like_count", np.nan), errors="coerce").mean(),
            "avg_best": df["best_count"].sum() / episodes,
            "베스트 댓글 합계": df["best_count"].sum(),
            "베스트 중 유료 비율(%)": df["best_paid"].sum() / df["best_count"].sum() * 100 if df["best_count"].sum() else 0,
        })
    return pd.DataFrame(rows)


def author_stats(comments: pd.DataFrame) -> pd.DataFrame:
    if comments.empty or "author" not in comments.columns:
        return pd.DataFrame()
    c = comments.copy()
    c["author"] = c["author"].fillna("").astype(str).str.strip()
    c = c[c["author"] != ""]
    if c.empty:
        return pd.DataFrame()
    c["is_preview_paid_comment"] = c["is_preview_paid_comment"].fillna(False).astype(bool)
    c["is_best"] = c["is_best"].fillna(False).astype(bool)
    c["like_count"] = pd.to_numeric(c["like_count"], errors="coerce").fillna(0)
    stats = c.groupby("author").agg(
        total_comments=("author", "size"),
        paid_comments=("is_preview_paid_comment", "sum"),
        best_comments=("is_best", "sum"),
        participated_episodes=("episode_no", "nunique"),
        like_sum=("like_count", "sum"),
    ).reset_index()
    stats["free_comments"] = stats["total_comments"] - stats["paid_comments"]
    stats["paid_ratio"] = np.where(stats["total_comments"] > 0, stats["paid_comments"] / stats["total_comments"] * 100, 0)
    stats["segment"] = pd.cut(
        stats["total_comments"],
        bins=[0, 1, 3, 9, np.inf],
        labels=["1회 작성자", "2~3회 작성자", "4~9회 작성자", "10회 이상 작성자"],
        include_lowest=True,
    )
    return stats.sort_values(["total_comments", "like_sum"], ascending=False)


def show_author_section(comments: pd.DataFrame, title: str):
    st.markdown("#### 작성자 기반 팬층 분석")
    stats = author_stats(comments)
    if stats.empty:
        st.info("작성자 데이터가 부족합니다.")
        return
    total_comments = int(stats["total_comments"].sum())
    unique_authors = int(len(stats))
    repeat_authors = int((stats["total_comments"] >= 2).sum())
    core_authors = int((stats["total_comments"] >= 5).sum())
    top10_share = stats.head(10)["total_comments"].sum() / total_comments * 100 if total_comments else 0
    comments_per_author = total_comments / unique_authors if unique_authors else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("작성자 수", f"{unique_authors:,}")
    c2.metric("댓글/작성자", f"{comments_per_author:.2f}", help="전체 댓글 수 ÷ 작성자 수입니다. 높을수록 소수 반복 작성자 비중이 큽니다.")
    c3.metric("2회 이상 작성자", f"{repeat_authors:,}")
    c4.metric("5회 이상 고관여", f"{core_authors:,}")
    c5.metric("상위 10명 댓글 비중", f"{top10_share:.1f}%")

    st.markdown("##### 작성자 반복 참여 구조")
    seg = stats.groupby("segment", observed=False).agg(작성자수=("author", "count"), 댓글수=("total_comments", "sum")).reset_index()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=seg["segment"].astype(str), y=seg["작성자수"], name="작성자 수"))
    fig.add_trace(go.Scatter(x=seg["segment"].astype(str), y=seg["댓글수"], name="댓글 수", mode="lines+markers", yaxis="y2"))
    fig.update_layout(
        title=f"{title} 작성자 반복 참여 구조",
        xaxis=dict(title="작성자 구간"),
        yaxis=dict(title="작성자 수"),
        yaxis2=dict(title="댓글 수", overlaying="y", side="right"),
        height=390,
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    render_plotly_chart(fig, use_container_width=True)

    st.markdown("##### 상위 작성자 댓글 수")
    safe_key = re.sub(r'[^0-9A-Za-z가-힣]+','_', title)
    top_n = st.slider("상위 작성자 표시 수", min_value=5, max_value=min(50, max(5, len(stats))), value=min(20, max(5, len(stats))), step=5, key=f"author_top_{safe_key}")
    top = stats.head(top_n).copy().sort_values("total_comments")
    fig_top = go.Figure()
    fig_top.add_trace(go.Bar(y=top["author"], x=top["free_comments"], name="일반 댓글", orientation="h"))
    fig_top.add_trace(go.Bar(y=top["author"], x=top["paid_comments"], name="유료 댓글", orientation="h"))
    fig_top.update_layout(
        title=f"{title} 작성자별 댓글 수 TOP {top_n}",
        xaxis=dict(title="댓글 수"),
        yaxis=dict(title="작성자", automargin=True),
        barmode="stack",
        height=max(420, 26 * top_n + 120),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    render_plotly_chart(fig_top, use_container_width=True)

    # Plotly 기본 Streamlit 차트는 클릭 이벤트를 직접 받기 어렵기 때문에,
    # 그래프 아래에서 작성자를 선택해 해당 작성자의 댓글을 모아보는 방식으로 제공합니다.
    top_authors = stats.head(top_n)["author"].astype(str).tolist()
    selected_author = st.selectbox(
        "댓글을 모아볼 작성자 선택",
        ["선택 안 함"] + top_authors,
        key=f"author_comment_view_{safe_key}",
        help="상위 작성자 그래프에 보이는 작성자 중 한 명을 선택하면 그 작성자의 댓글만 모아봅니다.",
    )
    if selected_author != "선택 안 함":
        view = comments.copy()
        view["author"] = view["author"].fillna("").astype(str).str.strip()
        view = view[view["author"] == selected_author].sort_values(["episode_no", "actual_written_at"])
        show_cols = ["episode_no", "episode_title", "author", "content", "written_at", "actual_written_at", "preview_comment_type", "is_best", "like_count", "reply_count"]
        show_cols = [c for c in show_cols if c in view.columns]
        st.markdown(f"##### {selected_author} 댓글 모아보기")
        st.dataframe(view[show_cols], use_container_width=True, hide_index=True)

    st.markdown("##### 회차별 작성자 수와 반복 작성자 댓글 비중")
    c = comments.copy()
    c["author"] = c["author"].fillna("").astype(str).str.strip()
    c = c[c["author"] != ""]
    if not c.empty:
        author_counts = stats.set_index("author")["total_comments"]
        c["author_total_comments"] = c["author"].map(author_counts)
        c["is_repeat_author"] = c["author_total_comments"] >= 2
        c["is_core_author"] = c["author_total_comments"] >= 5
        ep = c.groupby("episode_no").agg(
            comments=("author", "size"),
            unique_authors=("author", "nunique"),
            repeat_author_comments=("is_repeat_author", "sum"),
            core_author_comments=("is_core_author", "sum"),
        ).reset_index().sort_values("episode_no")
        ep["repeat_comment_ratio"] = np.where(ep["comments"] > 0, ep["repeat_author_comments"] / ep["comments"] * 100, 0)
        ep["core_comment_ratio"] = np.where(ep["comments"] > 0, ep["core_author_comments"] / ep["comments"] * 100, 0)
        fig_ep = make_subplots(specs=[[{"secondary_y": True}]])
        fig_ep.add_trace(go.Scatter(x=ep["episode_no"], y=ep["unique_authors"], mode="lines+markers", name="회차별 작성자 수"), secondary_y=False)
        fig_ep.add_trace(go.Scatter(x=ep["episode_no"], y=ep["repeat_comment_ratio"], mode="lines+markers", name="2회 이상 작성자 댓글 비중(%)", line=dict(dash="dash")), secondary_y=True)
        fig_ep.add_trace(go.Scatter(x=ep["episode_no"], y=ep["core_comment_ratio"], mode="lines+markers", name="5회 이상 작성자 댓글 비중(%)", line=dict(dash="dot")), secondary_y=True)
        base_layout(fig_ep, f"{title} 회차별 작성자 수와 반복 작성자 비중", "작성자 수", height=430)
        fig_ep.update_yaxes(title_text="반복 작성자 댓글 비중(%)", secondary_y=True)
        render_plotly_chart(fig_ep, use_container_width=True)

    with st.expander("작성자 상세 TOP 50"):
        st.dataframe(stats.head(50), use_container_width=True, hide_index=True)

def show_best_comment_table(comments: pd.DataFrame, key_prefix: str):
    if comments.empty:
        st.info("댓글 데이터가 없습니다.")
        return
    paid_filter = st.selectbox("유료/일반 필터", ["전체", "유료 댓글", "일반 댓글"], key=f"{key_prefix}_paid_filter")
    sort_by = st.selectbox("정렬", ["좋아요 수", "대댓글 수", "회차", "작성시점"], key=f"{key_prefix}_sort")
    view = comments.copy()
    if "is_best" in view.columns:
        view = view[view["is_best"].fillna(False).astype(bool)]
    if paid_filter == "유료 댓글":
        view = view[view["is_preview_paid_comment"].fillna(False).astype(bool)]
    elif paid_filter == "일반 댓글":
        view = view[~view["is_preview_paid_comment"].fillna(False).astype(bool)]
    sort_map = {"좋아요 수": "like_count", "대댓글 수": "reply_count", "회차": "episode_no", "작성시점": "actual_written_at"}
    col = sort_map[sort_by]
    ascending = sort_by in ["회차", "작성시점"]
    if col in view.columns:
        view = view.sort_values(col, ascending=ascending)
    display_cols = [
        "title_name", "episode_no", "episode_title", "author", "content", "written_at", "actual_written_at",
        "preview_comment_type", "like_count", "dislike_count", "reply_count", "comment_source",
    ]
    display_cols = [c for c in display_cols if c in view.columns]
    st.dataframe(view[display_cols].head(500), use_container_width=True, hide_index=True)


def get_release_datetime_for_comments(df: pd.DataFrame, release_hour=22, paid_offset_days=7, free_offset_days=0) -> pd.DataFrame:
    out = df.copy()
    if "free_release_at" in out.columns:
        base_release = pd.to_datetime(out["free_release_at"], errors="coerce")
    else:
        base_release = pd.Series(pd.NaT, index=out.index)
    fallback = pd.to_datetime(out.get("uploaded_at_dt", pd.NaT), errors="coerce").dt.normalize() + pd.to_timedelta(release_hour, unit="h")
    base_release = base_release.fillna(fallback)
    is_paid = out.get("is_preview_paid_comment", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    offset = np.where(is_paid, paid_offset_days, free_offset_days)
    out["release_dt"] = base_release - pd.to_timedelta(offset, unit="D")
    out["elapsed_hours"] = (pd.to_datetime(out["actual_written_at"], errors="coerce") - out["release_dt"]).dt.total_seconds() / 3600
    return out


def filter_comment_type_for_elapsed(df: pd.DataFrame, comment_type: str) -> pd.DataFrame:
    if comment_type == "유료 댓글":
        return df[df["is_preview_paid_comment"].fillna(False).astype(bool)].copy()
    if comment_type == "일반 댓글":
        return df[~df["is_preview_paid_comment"].fillna(False).astype(bool)].copy()
    return df.copy()


def make_elapsed_cumulative(raw: pd.DataFrame, episodes: List[int], comment_type: str, release_hour: int, paid_offset_days: int, free_offset_days: int, max_elapsed_hours: int, bucket_minutes: int) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["episode_no", "elapsed_bin", "comment_count", "cumulative_count"])
    work = filter_comment_type_for_elapsed(raw, comment_type)
    work = work[work["episode_no"].isin(episodes)].copy()
    work = get_release_datetime_for_comments(work, release_hour, paid_offset_days, free_offset_days)
    work = work[(work["elapsed_hours"] >= 0) & (work["elapsed_hours"] <= max_elapsed_hours)].copy()
    if work.empty:
        return pd.DataFrame(columns=["episode_no", "elapsed_bin", "comment_count", "cumulative_count"])
    work["elapsed_minutes"] = work["elapsed_hours"] * 60
    work["elapsed_bin"] = (work["elapsed_minutes"] // bucket_minutes * bucket_minutes).astype(int)
    agg = work.groupby(["episode_no", "elapsed_bin"]).size().reset_index(name="comment_count")
    max_elapsed_minutes = int(max_elapsed_hours * 60)
    bins = list(range(0, max_elapsed_minutes + 1, int(bucket_minutes)))
    full_index = pd.MultiIndex.from_product([sorted(episodes), bins], names=["episode_no", "elapsed_bin"])
    out = agg.set_index(["episode_no", "elapsed_bin"]).reindex(full_index, fill_value=0).reset_index()
    out["cumulative_count"] = out.groupby("episode_no")["comment_count"].cumsum()
    return out


def draw_elapsed_cumulative_heatmap(cum_elapsed: pd.DataFrame, title_sel: str, comment_type: str, key_prefix: str):
    if cum_elapsed.empty or cum_elapsed["cumulative_count"].max() == 0:
        st.info("선택한 조건에 해당하는 댓글이 없습니다. 공개일 계산 기준이나 확인 시간을 조정해보세요.")
        return
    h1, h2 = st.columns([1.2, 1.2])
    value_mode = h1.radio("히트맵 값", ["누적 댓글 수", "해당 구간 댓글 수"], horizontal=True, key=f"{key_prefix}_heatmap_value")
    normalize_mode = h2.radio("색상 스케일", ["원본값", "회차별 0~100 정규화"], horizontal=True, key=f"{key_prefix}_heatmap_scale")
    value_col = "cumulative_count" if value_mode == "누적 댓글 수" else "comment_count"
    heat = cum_elapsed.copy()
    heat["plot_value"] = pd.to_numeric(heat[value_col], errors="coerce").fillna(0).astype(float)
    heat["원본값"] = heat["plot_value"].copy()
    if normalize_mode == "회차별 0~100 정규화":
        max_by_ep = heat.groupby("episode_no")["plot_value"].transform("max")
        mask = max_by_ep > 0
        heat.loc[mask, "plot_value"] = heat.loc[mask, "plot_value"] / max_by_ep.loc[mask] * 100
    y_order = sorted(heat["episode_no"].dropna().astype(int).unique(), reverse=True)
    pivot = heat.pivot_table(index="episode_no", columns="elapsed_bin", values="plot_value", aggfunc="max", fill_value=0).reindex(y_order)
    raw_pivot = heat.pivot_table(index="episode_no", columns="elapsed_bin", values="원본값", aggfunc="max", fill_value=0).reindex(y_order)
    fig = go.Figure(data=go.Heatmap(
        x=list(pivot.columns), y=[f"{int(e)}화" for e in pivot.index], z=pivot.values, customdata=raw_pivot.values,
        colorscale="Blues", colorbar=dict(title=value_mode),
        hovertemplate="회차=%{y}<br>공개 후 경과 시간=%{x}분<br>표시값=%{z:.1f}<br>원본 댓글 수=%{customdata:.0f}<extra></extra>",
    ))
    fig.update_layout(title=f"{title_sel} — {comment_type} 공개 후 경과시간 히트맵", xaxis=dict(title="공개 후 경과 시간", ticksuffix="분"), yaxis=dict(title="회차"), height=max(430, min(900, 260 + len(pivot.index) * 12)))
    render_plotly_chart(fig, use_container_width=True)


def make_elapsed_cumulative_by_phase(raw: pd.DataFrame, comment_type: str, release_hour: int, paid_offset_days: int, free_offset_days: int, max_elapsed_hours: int, bucket_minutes: int, agg_mode: str = "평균") -> pd.DataFrame:
    """초기/중간/최근 구간별 공개 후 누적 댓글 합계/평균을 계산합니다."""
    if raw.empty:
        return pd.DataFrame(columns=["phase", "elapsed_bin", "value"])
    eps_all = sorted(raw["episode_no"].dropna().astype(int).unique().tolist())
    phase_map = {
        "초기 20%": get_segment_episodes(eps_all, "초기 20%"),
        "중간 60%": get_segment_episodes(eps_all, "중간 60%"),
        "최근 20%": get_segment_episodes(eps_all, "최근 20%"),
    }
    parts = []
    for phase, eps in phase_map.items():
        cum = make_elapsed_cumulative(raw, eps, comment_type, release_hour, paid_offset_days, free_offset_days, max_elapsed_hours, bucket_minutes)
        if cum.empty:
            continue
        if agg_mode == "합계":
            g = cum.groupby("elapsed_bin")["cumulative_count"].sum().reset_index(name="value")
        else:
            g = cum.groupby("elapsed_bin")["cumulative_count"].mean().reset_index(name="value")
        g["phase"] = phase
        parts.append(g)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["phase", "elapsed_bin", "value"])


def draw_cumulative_comment_section(title_sel: str, raw_comments: pd.DataFrame, key_prefix: str):
    st.markdown("#### 공개 후 작성 시점별 누적 댓글")
    if raw_comments.empty:
        st.info("댓글 데이터가 없습니다.")
        return
    available_eps = sorted(raw_comments["episode_no"].dropna().astype(int).unique().tolist())
    if not available_eps:
        st.warning("댓글 데이터에서 회차 정보를 찾지 못했습니다.")
        return
    default_start = max(min(available_eps), max(available_eps) - 9)
    ep_min, ep_max = st.slider("분석할 회차 범위", min_value=min(available_eps), max_value=max(available_eps), value=(default_start, max(available_eps)), key=f"{key_prefix}_cum_ep_range")
    selected_eps = [e for e in available_eps if ep_min <= e <= ep_max]
    c1, c2, c3, c4 = st.columns(4)
    comment_type = c1.radio("댓글 구분", ["유료 댓글", "일반 댓글", "유료+일반"], horizontal=True, key=f"{key_prefix}_cum_type")
    release_hour = c2.selectbox("공개 기준 시각", list(range(24)), index=22, format_func=lambda x: f"{x:02d}시", key=f"{key_prefix}_release_hour")
    max_elapsed_hours = c3.slider("공개 후 확인 시간", min_value=1, max_value=24, value=4, step=1, key=f"{key_prefix}_max_elapsed")
    bucket_minutes = c4.selectbox("집계 단위", [10, 20, 30, 60], index=0, format_func=lambda x: f"{x}분" if x < 60 else "1시간", key=f"{key_prefix}_bucket")
    with st.expander("공개일 계산 기준 조정"):
        p1, p2 = st.columns(2)
        paid_offset_days = p1.number_input("유료 댓글 공개일: 무료 공개일 기준 며칠 전", min_value=0, max_value=30, value=7, step=1, key=f"{key_prefix}_paid_offset")
        free_offset_days = p2.number_input("일반 댓글 공개일: 무료 공개일 기준 며칠 전", min_value=0, max_value=7, value=0, step=1, key=f"{key_prefix}_free_offset")
    cum = make_elapsed_cumulative(raw_comments, selected_eps, comment_type, release_hour, paid_offset_days, free_offset_days, max_elapsed_hours, bucket_minutes)

    # 누적 분석에 포함된 댓글 수를 실제 선택 회차 댓글 수와 함께 보여줍니다.
    selected_raw = filter_comment_type_for_elapsed(raw_comments[raw_comments["episode_no"].isin(selected_eps)].copy(), comment_type)
    included_count = 0 if cum.empty else int(cum.groupby("episode_no")["cumulative_count"].max().sum())
    total_selected = int(len(selected_raw))
    m1, m2, m3 = st.columns(3)
    m1.metric("선택 회차 실제 댓글 수", f"{total_selected:,}")
    m2.metric(f"공개 후 {max_elapsed_hours}시간 내 포함 댓글", f"{included_count:,}")
    m3.metric("포함 비율", f"{included_count / total_selected * 100:.1f}%" if total_selected else "0.0%")
    if total_selected > 0 and included_count == 0:
        st.warning("선택 회차에 댓글은 있지만 공개 후 경과시간 조건에 포함된 댓글이 없습니다. 공개 기준시각, 유료/일반 공개일 오프셋, 확인 시간을 조정해보세요.")

    t1, t2, t3, t4 = st.tabs(["누적 라인", "누적 히트맵", "초기/중간/최근 합계·평균", "데이터 표"])
    with t1:
        if cum.empty or cum["cumulative_count"].max() == 0:
            st.info("선택한 조건에 해당하는 댓글이 없습니다.")
        else:
            fig = go.Figure()
            for ep_no, g in cum.groupby("episode_no"):
                fig.add_trace(go.Scatter(x=g["elapsed_bin"], y=g["cumulative_count"], mode="lines+markers", name=f"{int(ep_no)}화"))
            base_layout(fig, f"{title_sel} — 공개 후 누적 댓글", "누적 댓글 수", x_title="공개 후 경과 시간(분)", height=460)
            render_plotly_chart(fig, use_container_width=True)
    with t2:
        draw_elapsed_cumulative_heatmap(cum, title_sel, comment_type, key_prefix=f"{key_prefix}_heatmap")
    with t3:
        agg_mode = st.radio("구간 집계 방식", ["평균", "합계"], horizontal=True, key=f"{key_prefix}_phase_agg")
        phase = make_elapsed_cumulative_by_phase(raw_comments, comment_type, release_hour, paid_offset_days, free_offset_days, max_elapsed_hours, bucket_minutes, agg_mode=agg_mode)
        if phase.empty:
            st.info("초기/중간/최근 구간별 누적 데이터를 만들 수 없습니다.")
        else:
            fig = go.Figure()
            for phase_name, g in phase.groupby("phase"):
                fig.add_trace(go.Scatter(x=g["elapsed_bin"], y=g["value"], mode="lines+markers", name=phase_name))
            base_layout(fig, f"{title_sel} 초기/중간/최근 공개 후 누적 댓글 — {agg_mode}", f"누적 댓글 {agg_mode}", x_title="공개 후 경과 시간(분)", height=460)
            render_plotly_chart(fig, use_container_width=True)
    with t4:
        st.dataframe(cum, use_container_width=True, hide_index=True)

def draw_all_metric_single_chart(df: pd.DataFrame, title_name: str, key_prefix: str):
    st.markdown("#### 전체 지표 통합 보기")
    if df.empty:
        st.info("표시할 데이터가 없습니다.")
        return
    metric_map = {
        "전체 댓글 수": "scraped",
        "유료 댓글 수": "paid",
        "일반 댓글 수": "free",
        "유료 댓글 비율(%)": "paid_ratio",
        "별점": "rating",
        "별점 참여자 수": "rating_count",
        "댓글 작성자 수": "unique_authors",
        "별점 참여자 중 댓글 작성자 비율(%)": "author_per_rating",
        "회차 좋아요 수": "episode_like_count",
        "베스트 댓글 수": "best_count",
        "댓글/별점참여(%)": "comment_per_rating",
    }
    default_metrics = ["전체 댓글 수", "유료 댓글 비율(%)", "별점 참여자 수", "회차 좋아요 수"]
    c1, c2, c3 = st.columns([2.2, 1.2, 1])
    selected = c1.multiselect("표시할 지표", list(metric_map.keys()), default=default_metrics, key=f"{key_prefix}_all_metric_select")
    scale = c2.selectbox("스케일 조정", ["0~100 정규화", "첫 회차=100 지수화", "원본값"], index=0, key=f"{key_prefix}_all_metric_scale")
    show_table = c3.checkbox("표 보기", value=False, key=f"{key_prefix}_all_metric_table")
    if not selected:
        st.info("표시할 지표를 하나 이상 선택해주세요.")
        return
    fig = go.Figure()
    tables = []
    for label in selected:
        col = metric_map[label]
        if col not in df.columns:
            continue
        plot_df = df[["episode_no", col]].copy().sort_values("episode_no")
        plot_df["scaled"] = scale_series(plot_df[col], scale)
        fig.add_trace(go.Scatter(x=plot_df["episode_no"], y=plot_df["scaled"], mode="lines+markers", name=label, customdata=plot_df[[col]].values, hovertemplate="회차=%{x}<br>표시값=%{y:.1f}<br>원본값=%{customdata[0]:,.2f}<extra>%{fullData.name}</extra>"))
        tables.append(plot_df.rename(columns={col: "원본값", "scaled": "표시값"}).assign(지표=label))
    base_layout(fig, f"{title_name} 전체 지표 통합 보기 — {scale}", "표시값" if scale != "원본값" else "원본값", height=520)
    if scale == "0~100 정규화":
        fig.update_yaxes(range=[-5, 105])
    render_plotly_chart(fig, use_container_width=True)
    if show_table and tables:
        st.dataframe(pd.concat(tables, ignore_index=True)[["지표", "episode_no", "원본값", "표시값"]], use_container_width=True, hide_index=True)



def _relative_segment_df(df: pd.DataFrame, segment: str, metric: str, name: str) -> pd.DataFrame:
    eps = get_segment_episodes(df["episode_no"], segment, None)
    out = df[df["episode_no"].isin(eps)].sort_values("episode_no").copy()
    out["segment_order"] = range(1, len(out) + 1)
    out["segment_len"] = len(out)
    # 길이가 다른 구간을 같은 x축 범위에 겹쳐 보기 위한 진행률 축입니다.
    # 예: 초기 20%가 17화, 중간 60%가 51화여도 모두 0~100% 구간에 압축해 표시합니다.
    if len(out) <= 1:
        out["segment_progress_pct"] = 0.0
    else:
        out["segment_progress_pct"] = (out["segment_order"] - 1) / (len(out) - 1) * 100
    out["구간"] = segment
    out["작품"] = name
    out["value"] = pd.to_numeric(out.get(metric, np.nan), errors="coerce")
    return out


def _segment_overlap_controls(metric_map: Dict[str, str], key_prefix: str):
    metric_label = st.selectbox("병치할 지표", list(metric_map.keys()), index=0, key=f"{key_prefix}_overlap_metric")
    scale = st.selectbox("표시 방식", ["원본값", "0~100 정규화", "첫 회차=100 지수화"], index=0, key=f"{key_prefix}_overlap_scale")
    x_mode = st.radio(
        "x축 방식",
        ["구간 진행률 0~100%", "구간 내 회차 순번"],
        index=0,
        horizontal=True,
        key=f"{key_prefix}_overlap_x_mode",
        help="중간 60%처럼 회차 수가 긴 구간을 초기/최근 20%와 같은 길이로 비교하려면 '구간 진행률 0~100%'를 사용하세요.",
    )
    segments = st.multiselect(
        "비교할 구간",
        ["초기 20%", "중간 60%", "최근 20%"],
        default=["초기 20%", "최근 20%"],
        key=f"{key_prefix}_overlap_segments",
        help="초기/중간/최근 구간을 같은 x축에 겹쳐 비교합니다. 구간 진행률을 쓰면 회차 수가 다른 구간도 같은 범위로 압축됩니다.",
    )
    return metric_label, metric_map[metric_label], scale, x_mode, segments


def _get_overlap_x(data: pd.DataFrame, x_mode: str):
    if x_mode == "구간 진행률 0~100%":
        return data["segment_progress_pct"], "구간 진행률", "%"
    return data["segment_order"], "각 구간 내 회차 순번", ""


def plot_segment_overlap_single(df: pd.DataFrame, title: str, key_prefix: str):
    st.markdown("#### 구간 병치 라인차트")
    st.caption(
        "중간 60%는 회차 수가 길기 때문에 기본값은 모든 구간을 0~100% 진행률 축으로 압축해 보여줍니다. "
        "이렇게 하면 초기 20%, 중간 60%, 최근 20%를 같은 x축 범위에서 병치할 수 있습니다."
    )
    if df.empty:
        return
    metric_map = {
        "전체 댓글 수": "scraped",
        "유료 댓글 비율(%)": "paid_ratio",
        "별점": "rating",
        "별점 참여자 수": "rating_count",
        "댓글 작성자 수": "unique_authors",
        "별점 참여자 중 댓글 작성자 비율(%)": "author_per_rating",
        "회차 좋아요 수": "episode_like_count",
        "베스트 댓글 수": "best_count",
    }
    metric_label, metric, scale, x_mode, segments = _segment_overlap_controls(metric_map, key_prefix)
    if not segments:
        st.info("비교할 구간을 하나 이상 선택해주세요.")
        return
    fig = go.Figure()
    x_title = "구간 진행률"
    ticksuffix = "%"
    for segment in segments:
        data = _relative_segment_df(df, segment, metric, title)
        y = data["value"] if scale == "원본값" else scale_series(data["value"], scale)
        x, x_title, ticksuffix = _get_overlap_x(data, x_mode)
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines+markers", name=f"{segment} ({len(data)}화)",
            customdata=data[["episode_no", "value", "segment_order", "segment_len"]].values,
            hovertemplate=(
                "x=%{x:.1f}" + ticksuffix + "<br>"
                "실제 회차=%{customdata[0]}화<br>"
                "구간 내 순번=%{customdata[2]}/%{customdata[3]}<br>"
                "표시값=%{y:,.2f}<br>원본값=%{customdata[1]:,.2f}"
                "<extra>%{fullData.name}</extra>"
            ),
        ))
    base_layout(fig, f"{title} 구간 병치 — {metric_label}", metric_label if scale == "원본값" else "변환값", x_title=x_title, height=450)
    if ticksuffix == "%":
        fig.update_xaxes(range=[0, 100], ticksuffix="%")
    render_plotly_chart(fig, use_container_width=True)


def plot_segment_overlap_compare(df1: pd.DataFrame, df2: pd.DataFrame, n1: str, n2: str, key_prefix: str):
    st.markdown("#### 구간 병치 비교")
    st.caption(
        "기본 x축은 구간 진행률 0~100%입니다. 회차 수가 다른 초기/중간/최근 구간을 같은 가로 범위로 압축해 비교합니다."
    )
    metric_map = {
        "전체 댓글 수": "scraped",
        "유료 댓글 비율(%)": "paid_ratio",
        "별점": "rating",
        "별점 참여자 수": "rating_count",
        "댓글 작성자 수": "unique_authors",
        "별점 참여자 중 댓글 작성자 비율(%)": "author_per_rating",
        "회차 좋아요 수": "episode_like_count",
        "베스트 댓글 수": "best_count",
    }
    metric_label, metric, scale, x_mode, segments = _segment_overlap_controls(metric_map, key_prefix)
    if not segments:
        st.info("비교할 구간을 하나 이상 선택해주세요.")
        return
    fig = go.Figure()
    dash_cycle = {"초기 20%": "solid", "중간 60%": "dash", "최근 20%": "dot"}
    x_title = "구간 진행률"
    ticksuffix = "%"
    for name, df in [(n1, df1), (n2, df2)]:
        for segment in segments:
            data = _relative_segment_df(df, segment, metric, name)
            y = data["value"] if scale == "원본값" else scale_series(data["value"], scale)
            x, x_title, ticksuffix = _get_overlap_x(data, x_mode)
            fig.add_trace(go.Scatter(
                x=x, y=y, mode="lines+markers", name=f"{name} {segment} ({len(data)}화)",
                line=dict(dash=dash_cycle.get(segment, "solid")),
                customdata=data[["episode_no", "value", "segment_order", "segment_len"]].values,
                hovertemplate=(
                    "x=%{x:.1f}" + ticksuffix + "<br>"
                    "실제 회차=%{customdata[0]}화<br>"
                    "구간 내 순번=%{customdata[2]}/%{customdata[3]}<br>"
                    "표시값=%{y:,.2f}<br>원본값=%{customdata[1]:,.2f}"
                    "<extra>%{fullData.name}</extra>"
                ),
            ))
    base_layout(fig, f"구간 병치 비교 — {metric_label}", metric_label if scale == "원본값" else "변환값", x_title=x_title, height=490)
    if ticksuffix == "%":
        fig.update_xaxes(range=[0, 100], ticksuffix="%")
    render_plotly_chart(fig, use_container_width=True)


# ════════════════════════════════════════════════════════════
# 사이드바 데이터 로드

# ════════════════════════════════════════════════════════════



# ════════════════════════════════════════════════════════════
# 통합 기능: 팬덤의존도 / 다운로드 수
# ════════════════════════════════════════════════════════════

PAYMENT_KEYWORDS_DEFAULT = "쿠키, 결제, 결재, 구웠, 굽는다, 질렀, 미리보기, 다음화, 유료"

DOWNLOAD_DATA_CANDIDATES = [
    BASE_DIR / "series_download_increase_report_history.csv",
    BASE_DIR / "data" / "series_download_increase_report_history.csv",
    BASE_DIR / "series_download_increase_report_history.xlsx",
    BASE_DIR / "data" / "series_download_increase_report_history.xlsx",
]

# increase_report_history에는 사후수집(post_24h)과 사전 기준값이 매칭된 행이 들어갑니다.
# snapshot_history에는 사전수집(pre_release)만 먼저 잡힌 작품도 들어갑니다.
# 따라서 특정 작품이 아직 사후수집 전이면 increase_report에는 없고 snapshot_history에만 있을 수 있습니다.
SNAPSHOT_DATA_CANDIDATES = [
    BASE_DIR / "series_download_snapshot_history.csv",
    BASE_DIR / "data" / "series_download_snapshot_history.csv",
    BASE_DIR / "series_download_snapshot_history.xlsx",
    BASE_DIR / "data" / "series_download_snapshot_history.xlsx",
]


def _ws_normalize_title(x: str) -> str:
    s = str(x or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[\[\]\(\){}〈〉《》<>_:;,.!?'\"~`·•\-–—]", "", s)
    return s


def _ws_fmt_int(x) -> str:
    if pd.isna(x):
        return "-"
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


def _ws_metric_card(label: str, value, fmt: str = "{:.3f}", help_text: Optional[str] = None):
    if pd.isna(value):
        st.metric(label, "-", help=help_text)
    elif isinstance(value, (int, np.integer)):
        st.metric(label, f"{value:,}", help=help_text)
    elif isinstance(value, float):
        st.metric(label, fmt.format(value), help=help_text)
    else:
        st.metric(label, str(value), help=help_text)


def _ws_safe_divide(num, den) -> np.ndarray:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return np.where(den > 0, num / den, np.nan)


def _ws_compile_keywords(keyword_text: str) -> List[str]:
    parts = re.split(r"[,\n/|]+", str(keyword_text or ""))
    return [p.strip() for p in parts if p.strip()]


def _ws_contains_keyword(series: pd.Series, keywords: List[str]) -> pd.Series:
    if not keywords:
        return pd.Series(False, index=series.index)
    pattern = "|".join(re.escape(k) for k in keywords)
    return series.fillna("").astype(str).str.contains(pattern, case=False, regex=True, na=False)


def _ws_classify_comment_flags(c: pd.DataFrame) -> pd.DataFrame:
    out = c.copy()
    ptype = out.get("preview_comment_type", pd.Series("", index=out.index)).fillna("").astype(str).str.strip()
    paid_raw = out.get("is_preview_paid_comment", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    out["is_paid_notebook"] = ptype.eq("유료결제 댓글") | ptype.str.contains("유료결제", na=False) | paid_raw
    out["is_free_after_notebook"] = (~out["is_paid_notebook"]) & (
        ptype.eq("무료공개 이후 댓글")
        | ptype.eq("일반 댓글")
        | ptype.str.contains("무료|일반", na=False)
        | ptype.eq("")
    )
    return out


def _ws_build_reference_time(c: pd.DataFrame, reference_hour: int = 23, backup_hour: int = 22) -> pd.Series:
    uploaded = pd.to_datetime(c.get("uploaded_at_dt", pd.NaT), errors="coerce")
    free_release = pd.to_datetime(c.get("free_release_at", pd.NaT), errors="coerce")
    ref = uploaded.dt.normalize() + pd.to_timedelta(reference_hour, unit="h")
    fallback = free_release.fillna(uploaded.dt.normalize() + pd.to_timedelta(backup_hour, unit="h"))
    return ref.fillna(fallback)


def ws_calculate_fandom_metrics(comments: pd.DataFrame, episodes: pd.DataFrame, initial_hours: float = 3.0, base_hours: float = 72.0, keyword_text: str = PAYMENT_KEYWORDS_DEFAULT) -> pd.DataFrame:
    if comments.empty:
        return pd.DataFrame()

    c = _ws_classify_comment_flags(comments)
    c["actual_written_at"] = pd.to_datetime(c["actual_written_at"], errors="coerce")
    c["reference_at"] = _ws_build_reference_time(c, reference_hour=23, backup_hour=22)
    c["after_initial"] = c["reference_at"] + pd.to_timedelta(initial_hours, unit="h")
    c["after_base"] = c["reference_at"] + pd.to_timedelta(base_hours, unit="h")
    c["elapsed_hours"] = (c["actual_written_at"] - c["reference_at"]).dt.total_seconds() / 3600

    c["in_base_window_notebook"] = c["actual_written_at"].notna() & c["after_base"].notna() & (c["actual_written_at"] <= c["after_base"])
    c["in_initial_window_notebook"] = (
        c["is_free_after_notebook"]
        & c["actual_written_at"].notna()
        & c["after_initial"].notna()
        & (c["actual_written_at"] <= c["after_initial"])
    )

    keywords = _ws_compile_keywords(keyword_text)
    c["has_payment_keyword"] = _ws_contains_keyword(c.get("content", pd.Series("", index=c.index)), keywords)
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

    summary["pre_response_rate"] = _ws_safe_divide(summary["paid_comments"], summary["base_72h_comments"])
    summary["initial_response_rate"] = _ws_safe_divide(summary["initial_comments"], summary["base_72h_comments"])
    summary["final_response_rate"] = _ws_safe_divide(summary["initial_comments"], summary["total_comments"])
    summary["fandom_dependency"] = summary["pre_response_rate"] - summary["initial_response_rate"]
    summary["payment_mention_rate"] = _ws_safe_divide(summary["payment_keyword_72h_comments"], summary["base_72h_comments"])

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


def _ws_classify_dependency_colors(values: pd.Series, red_top_pct: int = 25, blue_bottom_pct: int = 25) -> Tuple[List[str], float, float]:
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


def ws_plot_fandom_dependency(df: pd.DataFrame, title: str, key_prefix: str = "fandom"):
    data = df.sort_values("episode_no").copy()
    if data.empty:
        st.info("표시할 팬덤의존도 데이터가 없습니다.")
        return
    colors, red_cutoff, blue_cutoff = _ws_classify_dependency_colors(data["fandom_dependency"], 25, 25)
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
            "%{x}화<br>팬덤의존도=%{y:.3f}<br>"
            "유료결제 댓글=%{customdata[0]:,.0f}<br>"
            "초기 무료/일반 댓글=%{customdata[1]:,.0f}<br>"
            "72시간 기준 댓글=%{customdata[2]:,.0f}<br>"
            "전체 댓글=%{customdata[3]:,.0f}<br>"
            "사전호응률=%{customdata[4]:.3f}<br>"
            "초기호응률=%{customdata[5]:.3f}<br>"
            "최종호응률=%{customdata[6]:.3f}<br>"
            "결제언급률=%{customdata[7]:.3f}<extra></extra>"
        ),
    ))
    fig.add_hline(y=0, line_width=1, line_color="rgba(128,128,128,0.55)")
    fig.update_layout(
        title=f"{title} 회차별 팬덤의존도",
        xaxis_title="회차",
        yaxis_title="팬덤의존도",
        height=430,
        margin=dict(t=65, b=45, l=60, r=30),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.22)")
    render_plotly_chart(fig, use_container_width=True, download_key=f"{key_prefix}_fandom_dependency")
    if not pd.isna(red_cutoff) and not pd.isna(blue_cutoff):
        st.caption(f"색상 기준: 하위 25% ≤ {blue_cutoff:.3f} 파랑, 상위 25% ≥ {red_cutoff:.3f} 빨강, 나머지 회색.")


def ws_plot_response_rates(df: pd.DataFrame, title: str, key_prefix: str = "fandom"):
    data = df.sort_values("episode_no")
    if data.empty:
        return
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
    fig.add_hline(y=1, line_width=1, line_dash="dot", line_color="rgba(128,128,128,0.55)")
    fig.update_layout(
        title=f"{title} 회차별 호응률 지표",
        xaxis_title="회차",
        yaxis_title="지표값",
        height=460,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=80, b=50, l=60, r=30),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.22)")
    render_plotly_chart(fig, use_container_width=True, download_key=f"{key_prefix}_response_rates")


def ws_plot_fandom_counts(df: pd.DataFrame, title: str, key_prefix: str = "fandom"):
    data = df.sort_values("episode_no")
    if data.empty:
        return
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
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    render_plotly_chart(fig, use_container_width=True, download_key=f"{key_prefix}_fandom_counts")


def ws_show_fandom_section(title_sel: str, comments: pd.DataFrame, episodes: pd.DataFrame, key_prefix: str = "fandom"):
    st.markdown("#### 팬덤의존도")
    if comments.empty:
        st.info("댓글 데이터가 없어 팬덤의존도를 계산할 수 없습니다.")
        return
    summary = ws_calculate_fandom_metrics(comments, episodes)
    if summary.empty:
        st.info("계산 가능한 팬덤의존도 데이터가 없습니다.")
        return
    all_eps = sorted(summary["episode_no"].dropna().astype(int).unique())
    if not all_eps:
        st.info("회차 정보를 찾지 못했습니다.")
        return
    ep_range = st.slider("팬덤의존도 회차 범위", min_value=min(all_eps), max_value=max(all_eps), value=(min(all_eps), max(all_eps)), key=f"{key_prefix}_ep_range")
    view = summary[(summary["episode_no"] >= ep_range[0]) & (summary["episode_no"] <= ep_range[1])].copy()
    avg = view[["pre_response_rate", "initial_response_rate", "final_response_rate", "fandom_dependency", "payment_mention_rate"]].mean(numeric_only=True)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        _ws_metric_card("분석 회차", int(view["episode_no"].nunique()))
    with c2:
        _ws_metric_card("전체 댓글", int(view["total_comments"].sum()))
    with c3:
        _ws_metric_card("유료 댓글", int(view["paid_comments"].sum()))
    with c4:
        _ws_metric_card("72시간 기준 댓글", int(view["base_72h_comments"].sum()))
    with c5:
        _ws_metric_card("평균 팬덤의존도", float(avg.get("fandom_dependency", np.nan)))
    with c6:
        _ws_metric_card("평균 결제언급률", float(avg.get("payment_mention_rate", np.nan)))

    with st.expander("지표 정의", expanded=True):
        st.markdown(
            """
            | 지표 | 계산식 | 해석 |
            |---|---|---|
            | 사전호응률 | 유료결제 댓글 ÷ 72시간 댓글 | 팬덤 선반응 |
            | 초기호응률 | 공개 후 3시간 댓글 ÷ 72시간 댓글 | 무료 독자 유입 반응 |
            | 최종호응률 | 공개 후 3시간 댓글 ÷ 전체 댓글 | 장기 화제성 |
            | 팬덤의존도 | 사전호응률 - 초기호응률 | 유료 팬덤 의존도 |
            | 결제언급률 | 72시간 내 결제 키워드 댓글 ÷ 72시간 댓글 | 72시간 내 댓글 중 결제 관련 직접적 언급 비율 |
            """
        )

    ft1, ft2, ft3, ft4 = st.tabs(["팬덤의존도", "호응률 지표", "분모/분자 확인", "상세 데이터"])
    with ft1:
        st.caption("빨강 = 팬덤의존도 상위 회차, 파랑 = 초기 무료/일반 유입이 상대적으로 강한 회차, 회색 = 중간 구간입니다.")
        ws_plot_fandom_dependency(view, title_sel, key_prefix=key_prefix)
        left, right = st.columns(2)
        with left:
            st.markdown("##### 팬덤의존도 높은 회차 TOP 5")
            cols = ["episode_no", "episode_title", "fandom_dependency", "pre_response_rate", "initial_response_rate", "paid_comments", "base_72h_comments"]
            st.dataframe(view.sort_values("fandom_dependency", ascending=False)[[c for c in cols if c in view.columns]].head(5), use_container_width=True, hide_index=True)
        with right:
            st.markdown("##### 초기 유입이 강한 회차 TOP 5")
            cols = ["episode_no", "episode_title", "fandom_dependency", "pre_response_rate", "initial_response_rate", "initial_comments", "base_72h_comments"]
            st.dataframe(view.sort_values("fandom_dependency", ascending=True)[[c for c in cols if c in view.columns]].head(5), use_container_width=True, hide_index=True)
    with ft2:
        ws_plot_response_rates(view, title_sel, key_prefix=key_prefix)
    with ft3:
        ws_plot_fandom_counts(view, title_sel, key_prefix=key_prefix)
        st.info("72시간 기준 댓글은 actual_written_at <= after_72로 계산합니다. 따라서 유료결제 댓글도 72시간 기준 분모에 포함될 수 있습니다.")
    with ft4:
        display_cols = [
            "episode_no", "episode_title", "reference_at", "after_initial", "after_base",
            "paid_comments", "initial_comments", "base_72h_comments", "total_comments",
            "pre_response_rate", "initial_response_rate", "final_response_rate", "fandom_dependency",
            "payment_keyword_72h_comments", "payment_mention_rate",
            "rating", "rating_count", "episode_like_count", "best_comments", "unique_authors",
        ]
        display_cols = [c for c in display_cols if c in view.columns]
        st.dataframe(view[display_cols], use_container_width=True, hide_index=True)
        st.download_button(
            "팬덤의존도 CSV 다운로드",
            data=view[display_cols].to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{title_sel}_팬덤의존도.csv",
            mime="text/csv",
            key=f"{key_prefix}_download",
        )


def ws_find_download_file() -> Optional[Path]:
    for p in DOWNLOAD_DATA_CANDIDATES:
        if p.exists():
            return p
    return None


def ws_find_download_files() -> List[Path]:
    """대시보드용 다운로드 수 파일을 모두 찾습니다.

    - increase_report_history: 공개 전후 증가수 계산용
    - snapshot_history: 사전수집만 존재하는 작품의 누적 다운로드수 표시용

    같은 이벤트가 겹치면 standardize 단계에서 최신 행을 남깁니다.
    """
    files = []
    seen = set()
    for p in SNAPSHOT_DATA_CANDIDATES + DOWNLOAD_DATA_CANDIDATES:
        if p.exists():
            rp = p.resolve()
            if rp not in seen:
                files.append(p)
                seen.add(rp)
    return files


@st.cache_data(show_spinner="다운로드 수 데이터를 읽는 중입니다...")
def ws_load_download_data_from_path(path: str) -> pd.DataFrame:
    return read_table(Path(path))


@st.cache_data(show_spinner="업로드한 다운로드 수 파일을 읽는 중입니다...")
def ws_load_download_data_from_upload(uploaded_file) -> pd.DataFrame:
    return read_table(uploaded_file)


def ws_standardize_download_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["collected_at_kst", "baseline_collected_at_kst"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    for col in ["series_download_count", "baseline_download_count", "download_increase", "download_increase_rate"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ["webtoon_title", "release_weekday_ko", "release_weekday", "release_event_key"]:
        if col not in out.columns:
            out[col] = ""
    if "download_increase" not in out.columns:
        out["download_increase"] = np.nan
    if "baseline_download_count" in out.columns and "series_download_count" in out.columns:
        mask = out["download_increase"].isna() & out["series_download_count"].notna() & out["baseline_download_count"].notna()
        out.loc[mask, "download_increase"] = out.loc[mask, "series_download_count"] - out.loc[mask, "baseline_download_count"]
    if "collected_at_kst" in out.columns:
        out = out.sort_values("collected_at_kst")
    if "release_event_key" in out.columns and "collected_date_kst" in out.columns:
        dedupe_cols = [c for c in ["collected_date_kst", "release_event_key", "webtoon_title"] if c in out.columns]
        out = out.drop_duplicates(dedupe_cols, keep="last")
    return out.reset_index(drop=True)


def ws_filter_download_by_title(download_df: pd.DataFrame, title: str, meta_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    if download_df.empty or "webtoon_title" not in download_df.columns:
        return download_df.iloc[0:0].copy()
    d = download_df.copy()
    target_norm = _ws_normalize_title(title)
    d["_title_norm"] = d["webtoon_title"].fillna("").astype(str).map(_ws_normalize_title)

    candidates = {target_norm}
    if meta_df is not None and "display_name" in meta_df.columns:
        row = meta_df[meta_df["display_name"].astype(str).eq(str(title))]
        if not row.empty:
            for col in ["title_name"]:
                if col in row.columns:
                    candidates.add(_ws_normalize_title(row.iloc[0].get(col, "")))
            if "title_id" in row.columns and "webtoon_title_id" in d.columns and not pd.isna(row.iloc[0].get("title_id")):
                tid = int(row.iloc[0].get("title_id"))
                by_id = d[pd.to_numeric(d["webtoon_title_id"], errors="coerce").eq(tid)].copy()
                if not by_id.empty:
                    return by_id.drop(columns=["_title_norm"], errors="ignore")

    exact = d[d["_title_norm"].isin([c for c in candidates if c])].copy()
    if not exact.empty:
        return exact.drop(columns=["_title_norm"], errors="ignore")

    # 제목 문자열이 약간 다를 때의 보조 매칭
    mask = pd.Series(False, index=d.index)
    for cand in [c for c in candidates if c]:
        mask = mask | d["_title_norm"].str.contains(re.escape(cand), na=False) | pd.Series([cand in x for x in d["_title_norm"]], index=d.index)
    return d[mask].drop(columns=["_title_norm"], errors="ignore")


def ws_download_title_options(download_df: pd.DataFrame) -> List[str]:
    if download_df.empty or "webtoon_title" not in download_df.columns:
        return []
    return sorted([x for x in download_df["webtoon_title"].dropna().astype(str).str.strip().unique().tolist() if x])


def ws_build_download_event_table(df: pd.DataFrame) -> pd.DataFrame:
    """다운로드 수 수집 행을 대시보드용 이벤트 테이블로 바꿉니다.

    지원하는 원본은 2종입니다.
    1) increase_report_history: 사전수집값과 사후수집값이 한 행에 매칭된 파일
    2) snapshot_history: 각 수집 시점이 한 행씩 기록된 파일

    snapshot_history는 collection_context 값을 보고 pre_release는 사전수집,
    post_24h는 사후수집으로 분류합니다. baseline 컬럼이 없다는 이유로
    모든 collected_at_kst를 사후수집으로 취급하면 수요일 밤/목요일 공개 전
    기준값이 잘못 표시됩니다.
    """
    if df.empty:
        return pd.DataFrame()

    rows = []

    # 1) increase_report_history: 사전/사후가 한 행에 들어있는 경우
    if {"baseline_collected_at_kst", "baseline_download_count", "collected_at_kst", "series_download_count"}.issubset(df.columns):
        paired = df[df["baseline_collected_at_kst"].notna() | df["baseline_download_count"].notna()].copy()
        for _, r in paired.iterrows():
            rows.append({
                "공개요일": r.get("release_weekday_ko", ""),
                "사전수집시각": r.get("baseline_collected_at_kst", pd.NaT),
                "사전 누적 다운로드수": r.get("baseline_download_count", np.nan),
                "사후수집시각": r.get("collected_at_kst", pd.NaT),
                "사후 누적 다운로드수": r.get("series_download_count", np.nan),
                "공개 전후 증가수": r.get("download_increase", np.nan),
                "수집출처": r.get("_download_source_file", "increase_report_history"),
                "collection_context": r.get("collection_context", "paired_pre_post"),
            })

    # 2) snapshot_history: 수집 시점별 단일 행인 경우
    if {"collected_at_kst", "series_download_count"}.issubset(df.columns):
        snap = df.copy()
        if "baseline_collected_at_kst" in snap.columns:
            # paired increase 행은 위에서 이미 처리했으므로, baseline이 없는 snapshot 성격의 행만 추가
            snap = snap[snap["baseline_collected_at_kst"].isna()]

        for _, r in snap.iterrows():
            ctx = str(r.get("collection_context", "")).strip().lower()
            row = {
                "공개요일": r.get("release_weekday_ko", ""),
                "사전수집시각": pd.NaT,
                "사전 누적 다운로드수": np.nan,
                "사후수집시각": pd.NaT,
                "사후 누적 다운로드수": np.nan,
                "공개 전후 증가수": np.nan,
                "수집출처": r.get("_download_source_file", "snapshot_history"),
                "collection_context": r.get("collection_context", ""),
            }

            if ctx == "pre_release":
                row["사전수집시각"] = r.get("collected_at_kst", pd.NaT)
                row["사전 누적 다운로드수"] = r.get("series_download_count", np.nan)
            elif ctx == "post_24h":
                row["사후수집시각"] = r.get("collected_at_kst", pd.NaT)
                row["사후 누적 다운로드수"] = r.get("series_download_count", np.nan)
            else:
                # 과거 파일처럼 collection_context가 없으면 후행 호환을 위해 일반 수집점으로 표시
                row["사후수집시각"] = r.get("collected_at_kst", pd.NaT)
                row["사후 누적 다운로드수"] = r.get("series_download_count", np.nan)

            rows.append(row)

    tbl = pd.DataFrame(rows)
    if tbl.empty:
        return tbl

    for col in ["사전수집시각", "사후수집시각"]:
        if col in tbl.columns:
            tbl[col] = pd.to_datetime(tbl[col], errors="coerce")
    for col in ["사전 누적 다운로드수", "사후 누적 다운로드수", "공개 전후 증가수"]:
        if col in tbl.columns:
            tbl[col] = pd.to_numeric(tbl[col], errors="coerce")

    # 같은 파일을 여러 경로에서 읽거나, increase/snapshot이 겹쳐 같은 포인트가 중복되는 것을 완화
    sort_key = tbl["사전수집시각"].fillna(tbl["사후수집시각"])
    tbl = tbl.assign(_sort_key=sort_key).sort_values("_sort_key")
    dedupe_cols = ["공개요일", "사전수집시각", "사전 누적 다운로드수", "사후수집시각", "사후 누적 다운로드수"]
    tbl = tbl.drop_duplicates(subset=[c for c in dedupe_cols if c in tbl.columns], keep="last")
    return tbl.drop(columns=["_sort_key"], errors="ignore").reset_index(drop=True)


def ws_build_download_point_table(events: pd.DataFrame, title: str) -> pd.DataFrame:
    rows = []
    if events.empty:
        return pd.DataFrame(columns=["수집시각", "누적 다운로드수", "수집구분", "공개요일", "공개 전후 증가수"])
    for idx, r in events.iterrows():
        release_day = r.get("공개요일", "")
        inc = r.get("공개 전후 증가수", np.nan)
        pre_dt = r.get("사전수집시각", pd.NaT)
        pre_cnt = r.get("사전 누적 다운로드수", np.nan)
        if pd.notna(pre_dt) and pd.notna(pre_cnt):
            rows.append({"수집시각": pre_dt, "누적 다운로드수": pre_cnt, "수집구분": "사전수집", "공개요일": release_day, "공개 전후 증가수": inc, "웹툰": title, "이벤트순번": idx + 1})
        post_dt = r.get("사후수집시각", pd.NaT)
        post_cnt = r.get("사후 누적 다운로드수", np.nan)
        if pd.notna(post_dt) and pd.notna(post_cnt):
            rows.append({"수집시각": post_dt, "누적 다운로드수": post_cnt, "수집구분": "사후수집", "공개요일": release_day, "공개 전후 증가수": inc, "웹툰": title, "이벤트순번": idx + 1})
    pts = pd.DataFrame(rows)
    if pts.empty:
        return pts
    pts["수집시각"] = pd.to_datetime(pts["수집시각"], errors="coerce")
    pts["누적 다운로드수"] = pd.to_numeric(pts["누적 다운로드수"], errors="coerce")
    pts["공개 전후 증가수"] = pd.to_numeric(pts["공개 전후 증가수"], errors="coerce")
    pts = pts.dropna(subset=["수집시각", "누적 다운로드수"])
    pts = pts.drop_duplicates(subset=["수집시각", "누적 다운로드수", "수집구분", "웹툰"], keep="last")
    pts = pts.sort_values("수집시각").reset_index(drop=True)
    return pts


def ws_plot_download_prepost_line(points: pd.DataFrame, title: str, key_prefix: str = "download"):
    if points.empty:
        st.info("표시할 수집 시점 데이터가 없습니다.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=points["수집시각"],
        y=points["누적 다운로드수"],
        mode="lines",
        name="누적 다운로드 흐름",
        line=dict(width=2.6),
        hoverinfo="skip",
    ))
    marker_specs = {"사전수집": dict(symbol="circle", size=10), "사후수집": dict(symbol="diamond", size=11)}
    for phase in ["사전수집", "사후수집"]:
        g = points[points["수집구분"] == phase].copy()
        if g.empty:
            continue
        custom = np.stack([
            g["수집구분"].astype(str),
            g["공개요일"].fillna("").astype(str),
            g["공개 전후 증가수"].map(_ws_fmt_int),
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
                "공개 전후 증가수=%{customdata[2]}<extra></extra>"
            ),
        ))
    tick_points = points.drop_duplicates("수집시각").sort_values("수집시각").copy()
    tick_vals = tick_points["수집시각"].tolist()
    tick_text = tick_points["수집시각"].dt.strftime("%m/%d<br>%H:%M").tolist()
    fig.update_layout(
        title=f"{title} — 사전/사후 수집 시점별 누적 다운로드수",
        height=560,
        margin=dict(t=80, b=70, l=80, r=40),
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
        xaxis=dict(title="수집 시각", showgrid=True, tickmode="array", tickvals=tick_vals, ticktext=tick_text, tickangle=0),
        yaxis=dict(title="누적 다운로드수", tickformat=","),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    render_plotly_chart(fig, use_container_width=True, download_key=f"{key_prefix}_download_line")


def ws_plot_download_increase_bar(events: pd.DataFrame, title: str, key_prefix: str = "download"):
    if events.empty or "공개 전후 증가수" not in events.columns:
        return
    d = events.dropna(subset=["공개 전후 증가수"]).copy()
    if d.empty:
        return
    pre_dt = pd.to_datetime(d.get("사전수집시각"), errors="coerce")
    post_dt = pd.to_datetime(d.get("사후수집시각"), errors="coerce")
    d["이벤트"] = [
        f"{a.strftime('%m/%d %H:%M') if pd.notna(a) else '-'} → {b.strftime('%m/%d %H:%M') if pd.notna(b) else '-'}"
        for a, b in zip(pre_dt, post_dt)
    ]
    d["hover_pre"] = pre_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("-")
    d["hover_post"] = post_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("-")
    custom = np.stack([
        d["hover_pre"].astype(str),
        d["hover_post"].astype(str),
        d.get("공개요일", "").astype(str) if "공개요일" in d.columns else pd.Series([""] * len(d)),
    ], axis=-1)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=d["이벤트"],
        y=d["공개 전후 증가수"],
        name="공개 전후 증가수",
        width=[0.28] * len(d),
        customdata=custom,
        hovertemplate="사전수집=%{customdata[0]}<br>사후수집=%{customdata[1]}<br>공개요일=%{customdata[2]}<br>증가수=%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        title=f"{title} — 공개 전후 증가수",
        height=330,
        margin=dict(t=70, b=95, l=80, r=40),
        bargap=0.75,
        xaxis=dict(title="공개 이벤트", type="category", tickangle=0),
        yaxis=dict(title="증가수", tickformat=","),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    render_plotly_chart(fig, use_container_width=True, download_key=f"{key_prefix}_download_bar")


def ws_show_download_metrics(points: pd.DataFrame, events: pd.DataFrame):
    latest_download = points["누적 다운로드수"].dropna().iloc[-1] if not points.empty else np.nan
    recent_inc = events["공개 전후 증가수"].dropna().iloc[-1] if not events.empty and "공개 전후 증가수" in events.columns and not events["공개 전후 증가수"].dropna().empty else np.nan
    event_count = len(events)
    c1, c2, c3 = st.columns(3)
    c1.metric("최근 누적 다운로드수", _ws_fmt_int(latest_download))
    c2.metric("최근 공개 전후 증가수", _ws_fmt_int(recent_inc))
    c3.metric("공개 이벤트 수", f"{event_count:,}")


def ws_get_download_data(upload_key: str = "download_file") -> pd.DataFrame:
    file_paths = ws_find_download_files()
    if file_paths:
        frames = []
        for p in file_paths:
            try:
                tmp = ws_load_download_data_from_path(str(p))
                tmp["_download_source_file"] = p.name
                frames.append(tmp)
            except Exception as e:
                st.warning(f"다운로드 수 파일을 읽지 못했습니다: {p.name} / {e}")
        if frames:
            raw = pd.concat(frames, ignore_index=True, sort=False)
            return ws_standardize_download_df(raw)

    uploaded = st.file_uploader("series_download_increase_report_history 또는 series_download_snapshot_history 파일 업로드", type=["csv", "xlsx", "xls"], key=upload_key)
    if uploaded is None:
        return pd.DataFrame()
    raw = ws_load_download_data_from_upload(uploaded)
    return ws_standardize_download_df(raw)


def ws_show_download_section(title_sel: str, meta_df: Optional[pd.DataFrame] = None, key_prefix: str = "download"):
    st.markdown("#### 다운로드 수")
    download_df = ws_get_download_data(upload_key=f"{key_prefix}_upload")
    if download_df.empty:
        st.info("`series_download_increase_report_history.csv` 또는 `series_download_snapshot_history.csv` 파일을 대시보드 파일과 같은 폴더 또는 data 폴더에 두면 자동으로 읽습니다.")
        return
    d = ws_filter_download_by_title(download_df, title_sel, meta_df)
    if d.empty:
        st.warning(f"'{title_sel}'에 매칭되는 다운로드 수 데이터를 찾지 못했습니다.")
        with st.expander("다운로드 데이터에 있는 웹툰명 보기", expanded=False):
            opts = ws_download_title_options(download_df)
            st.write(opts[:300])
        return
    events_tbl = ws_build_download_event_table(d)
    points_tbl = ws_build_download_point_table(events_tbl, title_sel)
    ws_show_download_metrics(points_tbl, events_tbl)
    st.divider()
    ws_plot_download_prepost_line(points_tbl, title_sel, key_prefix=key_prefix)
    ws_plot_download_increase_bar(events_tbl, title_sel, key_prefix=key_prefix)
    st.markdown("##### 공개 전후 수집 데이터")
    if events_tbl.empty:
        st.info("표시할 데이터가 없습니다.")
    else:
        display = events_tbl.copy()
        for col in ["사전수집시각", "사후수집시각"]:
            if col in display.columns:
                display[col] = pd.to_datetime(display[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        for col in ["사전 누적 다운로드수", "사후 누적 다운로드수", "공개 전후 증가수"]:
            if col in display.columns:
                display[col] = pd.to_numeric(display[col], errors="coerce").map(_ws_fmt_int)
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.download_button(
            "다운로드 수 CSV 다운로드",
            data=events_tbl.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{title_sel}_download_prepost.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv_download",
        )


def ws_show_download_compare_section(title_a: str, title_b: str, meta_df: Optional[pd.DataFrame] = None, key_prefix: str = "compare_download"):
    st.markdown("#### 다운로드 수 비교")
    download_df = ws_get_download_data(upload_key=f"{key_prefix}_upload")
    if download_df.empty:
        st.info("`series_download_increase_report_history.csv` 또는 `series_download_snapshot_history.csv` 파일을 대시보드 파일과 같은 폴더 또는 data 폴더에 두면 자동으로 읽습니다.")
        return

    prepared = []
    missing = []
    for title in [title_a, title_b]:
        d = ws_filter_download_by_title(download_df, title, meta_df)
        if d.empty:
            missing.append(title)
            continue
        events = ws_build_download_event_table(d)
        points = ws_build_download_point_table(events, title)
        prepared.append((title, events, points))

    if missing:
        st.warning("다운로드 수 데이터와 매칭되지 않은 작품: " + ", ".join(missing))
        with st.expander("다운로드 수 데이터에 있는 웹툰명 보기", expanded=False):
            st.write(ws_download_title_options(download_df)[:300])
    if not prepared:
        return

    metric_rows = []
    for title, events, points in prepared:
        latest_download = points["누적 다운로드수"].dropna().iloc[-1] if not points.empty else np.nan
        recent_inc = events["공개 전후 증가수"].dropna().iloc[-1] if not events.empty and "공개 전후 증가수" in events.columns and not events["공개 전후 증가수"].dropna().empty else np.nan
        total_inc = events["공개 전후 증가수"].dropna().sum() if not events.empty and "공개 전후 증가수" in events.columns else np.nan
        metric_rows.append({
            "작품": title,
            "최근 누적 다운로드수": latest_download,
            "최근 공개 전후 증가수": recent_inc,
            "공개 전후 증가수 합계": total_inc,
            "공개 이벤트 수": len(events),
        })
    metric_df = pd.DataFrame(metric_rows)
    display_metrics = metric_df.copy()
    for col in ["최근 누적 다운로드수", "최근 공개 전후 증가수", "공개 전후 증가수 합계"]:
        display_metrics[col] = pd.to_numeric(display_metrics[col], errors="coerce").map(_ws_fmt_int)
    st.dataframe(display_metrics, use_container_width=True, hide_index=True)

    st.markdown("##### 사전/사후 수집 시점별 누적 다운로드수 비교")
    all_points = []
    for title, events, points in prepared:
        if points.empty:
            continue
        pts = points.copy()
        pts["작품"] = title
        all_points.append(pts)
    if all_points:
        pts_all = pd.concat(all_points, ignore_index=True).sort_values("수집시각")
        fig = go.Figure()
        for title, g in pts_all.groupby("작품", sort=False):
            custom = np.stack([
                g["수집구분"].astype(str),
                g["공개요일"].fillna("").astype(str),
                g["공개 전후 증가수"].map(_ws_fmt_int),
            ], axis=-1)
            fig.add_trace(go.Scatter(
                x=g["수집시각"],
                y=g["누적 다운로드수"],
                mode="lines+markers",
                name=title,
                customdata=custom,
                hovertemplate=(
                    "작품=%{fullData.name}<br>"
                    "수집시각=%{x|%Y-%m-%d %H:%M}<br>"
                    "구분=%{customdata[0]}<br>"
                    "공개요일=%{customdata[1]}<br>"
                    "누적 다운로드수=%{y:,.0f}<br>"
                    "공개 전후 증가수=%{customdata[2]}<extra></extra>"
                ),
            ))
        tick_points = pts_all.drop_duplicates("수집시각").sort_values("수집시각")
        fig.update_layout(
            title=f"{title_a} vs {title_b} — 누적 다운로드수",
            height=520,
            margin=dict(t=80, b=70, l=80, r=40),
            hovermode="closest",
            legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
            xaxis=dict(
                title="수집 시각",
                showgrid=True,
                tickmode="array",
                tickvals=tick_points["수집시각"].tolist(),
                ticktext=tick_points["수집시각"].dt.strftime("%m/%d<br>%H:%M").tolist(),
            ),
            yaxis=dict(title="누적 다운로드수", tickformat=","),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        render_plotly_chart(fig, use_container_width=True, download_key=f"{key_prefix}_line")
    else:
        st.info("표시할 누적 다운로드 수 포인트가 없습니다.")

    st.markdown("##### 공개 전후 증가수 비교")
    fig_bar = go.Figure()
    has_bar = False
    for title, events, points in prepared:
        if events.empty or "공개 전후 증가수" not in events.columns:
            continue
        d = events.dropna(subset=["공개 전후 증가수"]).copy().reset_index(drop=True)
        if d.empty:
            continue
        has_bar = True
        d["이벤트순번"] = np.arange(1, len(d) + 1)
        pre_dt = pd.to_datetime(d.get("사전수집시각"), errors="coerce")
        post_dt = pd.to_datetime(d.get("사후수집시각"), errors="coerce")
        custom = np.stack([
            pre_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("-"),
            post_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("-"),
            d.get("공개요일", pd.Series([""] * len(d))).astype(str),
        ], axis=-1)
        fig_bar.add_trace(go.Bar(
            x=d["이벤트순번"],
            y=d["공개 전후 증가수"],
            name=title,
            width=0.32,
            customdata=custom,
            hovertemplate=(
                "작품=%{fullData.name}<br>"
                "이벤트순번=%{x}<br>"
                "사전수집=%{customdata[0]}<br>"
                "사후수집=%{customdata[1]}<br>"
                "공개요일=%{customdata[2]}<br>"
                "증가수=%{y:,.0f}<extra></extra>"
            ),
        ))
    if has_bar:
        fig_bar.update_layout(
            title=f"{title_a} vs {title_b} — 공개 전후 증가수",
            height=360,
            margin=dict(t=70, b=60, l=80, r=40),
            barmode="group",
            bargap=0.45,
            xaxis=dict(title="공개 이벤트 순번", tickmode="linear", dtick=1),
            yaxis=dict(title="증가수", tickformat=","),
            legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="left", x=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        render_plotly_chart(fig_bar, use_container_width=True, download_key=f"{key_prefix}_bar")

    st.markdown("##### 다운로드 수 비교 데이터")
    event_rows = []
    for title, events, points in prepared:
        e = events.copy()
        if e.empty:
            continue
        e.insert(0, "작품", title)
        event_rows.append(e)
    if event_rows:
        table = pd.concat(event_rows, ignore_index=True)
        display = table.copy()
        for col in ["사전수집시각", "사후수집시각"]:
            if col in display.columns:
                display[col] = pd.to_datetime(display[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        for col in ["사전 누적 다운로드수", "사후 누적 다운로드수", "공개 전후 증가수"]:
            if col in display.columns:
                display[col] = pd.to_numeric(display[col], errors="coerce").map(_ws_fmt_int)
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.download_button(
            "다운로드 수 비교 CSV 다운로드",
            data=table.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{title_a}_vs_{title_b}_download_compare.csv",
            mime="text/csv",
            key=f"{key_prefix}_csv",
        )


def ws_show_download_page():
    st.title("다운로드 수")
    download_df = ws_get_download_data(upload_key="download_page_upload")
    if download_df.empty:
        st.info("`series_download_increase_report_history.csv` 또는 `series_download_snapshot_history.csv` 파일을 대시보드 파일과 같은 폴더 또는 data 폴더에 두면 자동으로 읽습니다.")
        return
    opts = ws_download_title_options(download_df)
    if not opts:
        st.error("다운로드 데이터에서 webtoon_title 컬럼을 찾지 못했습니다.")
        return
    selected = st.selectbox("웹툰 선택", opts, key="download_page_title")
    d = ws_filter_download_by_title(download_df, selected)
    events_tbl = ws_build_download_event_table(d)
    points_tbl = ws_build_download_point_table(events_tbl, selected)
    ws_show_download_metrics(points_tbl, events_tbl)
    st.divider()
    ws_plot_download_prepost_line(points_tbl, selected, key_prefix="download_page")
    ws_plot_download_increase_bar(events_tbl, selected, key_prefix="download_page")
    st.markdown("### 공개 전후 수집 데이터")
    if not events_tbl.empty:
        display = events_tbl.copy()
        for col in ["사전수집시각", "사후수집시각"]:
            if col in display.columns:
                display[col] = pd.to_datetime(display[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        for col in ["사전 누적 다운로드수", "사후 누적 다운로드수", "공개 전후 증가수"]:
            if col in display.columns:
                display[col] = pd.to_numeric(display[col], errors="coerce").map(_ws_fmt_int)
        st.dataframe(display, use_container_width=True, hide_index=True)

st.sidebar.header("🧭 메뉴")

mode = st.sidebar.radio(
    "화면 선택",
    ["패치노트", "데이터 개요", "단일 작품 모니터링", "두 웹툰 비교"],
)

with st.sidebar.expander("차트 다운로드 설정", expanded=False):
    st.radio(
        "Plotly HTML 저장 방식",
        ["가벼운 파일(CDN)", "오프라인용(용량 큼)"],
        index=0,
        key="plotly_html_download_mode",
        help="차트 아래 HTML 다운로드 버튼에서 사용할 저장 방식입니다.",
    )

# 이 대시보드는 같은 GitHub repo 안의 파일을 자동 감지해 읽는 것을 기본으로 합니다.
# 파일 업로드 UI는 Streamlit Cloud/배포 화면을 단순하게 유지하기 위해 제거했습니다.
comment_uploads = None
episode_uploads = None
meta_uploads = None

comments_all, episodes_all, meta_raw, comment_files, episode_files = load_all_data(comment_uploads, episode_uploads, meta_uploads)

if comments_all.empty and episodes_all.empty and mode != "패치노트":
    st.title("웹툰 반응 비교 대시보드")
    st.info(
        "데이터 파일을 찾지 못했습니다. 대시보드 파일과 같은 폴더 또는 data/dashboard_data 등 하위 폴더에 댓글/회차정보 CSV, XLSX 파일을 두면 자동으로 읽습니다."
    )
    st.stop()

summary_all = build_episode_summary(comments_all, episodes_all) if not (comments_all.empty and episodes_all.empty) else pd.DataFrame()
webtoon_meta = build_webtoon_meta(comments_all, episodes_all, meta_raw) if not (comments_all.empty and episodes_all.empty) else pd.DataFrame()

if webtoon_meta.empty and mode != "패치노트":
    st.warning("웹툰 목록을 만들 수 없습니다. title_name 또는 title_id 컬럼을 확인해주세요.")
    st.stop()

if mode != "패치노트":
    st.sidebar.success(f"댓글 파일 {len(comment_files)}개, 회차정보 파일 {len(episode_files)}개 감지")


# ════════════════════════════════════════════════════════════
# 공통 선택 함수
# ════════════════════════════════════════════════════════════

def segment_controls(prefix: str, available_eps: List[int]) -> Tuple[str, Optional[Tuple[int, int]]]:
    seg = st.selectbox(
        "분석 구간",
        ["전체", "초기 20%", "중간 60%", "최근 20%", "사용자 지정"],
        index=0,
        key=f"{prefix}_segment",
        help="초기/중간/최근 구간은 각 웹툰의 공개 회차 수를 기준으로 따로 계산합니다.",
    )
    custom = None
    if seg == "사용자 지정" and available_eps:
        mn, mx = min(available_eps), max(available_eps)
        custom = st.slider("사용자 지정 회차 범위", mn, mx, (mn, mx), key=f"{prefix}_custom_range")
    return seg, custom


def get_title_options(filtered_meta: Optional[pd.DataFrame] = None) -> List[str]:
    df = webtoon_meta if filtered_meta is None else filtered_meta
    return sorted(df["display_name"].dropna().astype(str).unique().tolist())


def common_tags_for_title(title_name: str) -> List[str]:
    row = webtoon_meta[webtoon_meta["display_name"] == title_name]
    if row.empty:
        return []
    r = row.iloc[0]
    tags = split_tags(r.get("tags", ""))
    genre = split_tags(r.get("genre", ""))
    return sorted(set(tags + genre))


def multi_choice_click(label: str, options: List[str], key: str, default=None, help: Optional[str] = None) -> List[str]:
    """Streamlit 버전에 따라 pills가 있으면 클릭형, 없으면 multiselect로 대체합니다."""
    default = default or []
    if hasattr(st, "pills"):
        return st.pills(label, options, selection_mode="multi", default=default, key=key, help=help) or []
    return st.multiselect(label, options, default=default, key=key, help=help)



# ════════════════════════════════════════════════════════════
# 패치노트/상태 표시용 헬퍼
# ════════════════════════════════════════════════════════════

MAPPING_DATA_CANDIDATES = [
    BASE_DIR / "data" / "naver_webtoon_series_productNo_mapping_v2_title_cleaned.csv",
    BASE_DIR / "data" / "naver_webtoon_series_productNo_mapping_v2_title_cleaned.xlsx",
    BASE_DIR / "data" / "naver_webtoon_series_mapping_dedup_cleaned.csv",
    BASE_DIR / "data" / "naver_webtoon_series_mapping_dedup_cleaned.xlsx",
    BASE_DIR / "data" / "naver_webtoon_series_productNo_mapping.csv",
    BASE_DIR / "data" / "naver_webtoon_series_productNo_mapping.xlsx",
    BASE_DIR / "naver_webtoon_series_productNo_mapping.csv",
    BASE_DIR / "naver_webtoon_series_productNo_mapping.xlsx",
]


def _ws_fmt_datetime_kst(x) -> str:
    dt = pd.to_datetime(x, errors="coerce")
    if pd.isna(dt):
        return "-"
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


@st.cache_data(show_spinner=False)
def ws_get_download_update_status() -> Dict[str, object]:
    """repo 안의 다운로드 수 파일을 읽어 최종 수집 시각을 계산합니다."""
    files = []
    seen = set()
    for p in SNAPSHOT_DATA_CANDIDATES + DOWNLOAD_DATA_CANDIDATES:
        if p.exists():
            rp = str(p.resolve())
            if rp not in seen:
                files.append(p)
                seen.add(rp)

    frames = []
    for p in files:
        try:
            df = read_table(p)
            df["_status_source_file"] = p.name
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return {
            "download_file_count": 0,
            "download_row_count": 0,
            "download_webtoon_count": 0,
            "latest_download_collected_at": pd.NaT,
            "download_source_files": "",
        }

    df_all = pd.concat(frames, ignore_index=True, sort=False)

    latest = pd.NaT
    for col in ["collected_at_kst", "baseline_collected_at_kst", "collected_at"]:
        if col in df_all.columns:
            cand = pd.to_datetime(df_all[col], errors="coerce").max()
            if pd.notna(cand) and (pd.isna(latest) or cand > latest):
                latest = cand

    if "webtoon_title_id" in df_all.columns:
        webtoon_count = pd.to_numeric(df_all["webtoon_title_id"], errors="coerce").dropna().nunique()
    elif "title_id" in df_all.columns:
        webtoon_count = pd.to_numeric(df_all["title_id"], errors="coerce").dropna().nunique()
    elif "webtoon_title" in df_all.columns:
        webtoon_count = df_all["webtoon_title"].dropna().astype(str).str.strip().replace("", np.nan).dropna().nunique()
    else:
        webtoon_count = 0

    return {
        "download_file_count": len(files),
        "download_row_count": len(df_all),
        "download_webtoon_count": int(webtoon_count),
        "latest_download_collected_at": latest,
        "download_source_files": ", ".join([p.name for p in files]),
    }


@st.cache_data(show_spinner=False)
def ws_get_mapping_update_status() -> Dict[str, object]:
    """repo 안의 웹툰-시리즈 매핑 파일을 읽어 최종 매핑 업데이트 시각을 계산합니다."""
    files = []
    seen = set()
    for p in MAPPING_DATA_CANDIDATES:
        if p.exists():
            rp = str(p.resolve())
            if rp not in seen:
                files.append(p)
                seen.add(rp)

    frames = []
    for p in files:
        try:
            df = read_table(p)
            df["_status_source_file"] = p.name
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return {
            "mapping_file_count": 0,
            "mapping_row_count": 0,
            "mapping_webtoon_count": 0,
            "latest_mapping_updated_at": pd.NaT,
            "mapping_source_files": "",
        }

    df_all = pd.concat(frames, ignore_index=True, sort=False)

    latest = pd.NaT
    for col in ["mapping_updated_at_kst", "collected_at_kst", "updated_at_kst"]:
        if col in df_all.columns:
            cand = pd.to_datetime(df_all[col], errors="coerce").max()
            if pd.notna(cand) and (pd.isna(latest) or cand > latest):
                latest = cand

    if "webtoon_title_id" in df_all.columns:
        webtoon_count = pd.to_numeric(df_all["webtoon_title_id"], errors="coerce").dropna().nunique()
    elif "title_id" in df_all.columns:
        webtoon_count = pd.to_numeric(df_all["title_id"], errors="coerce").dropna().nunique()
    elif "webtoon_title" in df_all.columns:
        webtoon_count = df_all["webtoon_title"].dropna().astype(str).str.strip().replace("", np.nan).dropna().nunique()
    else:
        webtoon_count = 0

    return {
        "mapping_file_count": len(files),
        "mapping_row_count": len(df_all),
        "mapping_webtoon_count": int(webtoon_count),
        "latest_mapping_updated_at": latest,
        "mapping_source_files": ", ".join([p.name for p in files]),
    }



# ════════════════════════════════════════════════════════════
# 화면 0: 패치노트
# ════════════════════════════════════════════════════════════

if mode == "패치노트":
    st.title("웹툰스테이션 대시보드")
    st.caption("웹툰 반응, 팬덤의존도, 다운로드 수를 한 화면 안에서 확인하는 통합 대시보드입니다.")

    download_status = ws_get_download_update_status()
    mapping_status = ws_get_mapping_update_status()

    st.markdown("### 데이터 업데이트 상태")
    u1, u2, u3, u4 = st.columns(4)
    u1.metric("마지막 다운로드 수 수집", _ws_fmt_datetime_kst(download_status.get("latest_download_collected_at")))
    u2.metric("다운로드 수 대상 웹툰", f"{int(download_status.get('download_webtoon_count', 0)):,}")
    u3.metric("다운로드 수 데이터 행", f"{int(download_status.get('download_row_count', 0)):,}")
    u4.metric("마지막 목록 업데이트", _ws_fmt_datetime_kst(mapping_status.get("latest_mapping_updated_at")))

    with st.expander("읽고 있는 다운로드 수/매핑 파일", expanded=False):
        st.write("다운로드 수 파일:", download_status.get("download_source_files") or "없음")
        st.write("매핑 파일:", mapping_status.get("mapping_source_files") or "없음")

    st.markdown("### 260627 업데이트")
    st.markdown(
        """
        - **다운로드 수 추적 기능 추가**: 네이버 시리즈의 작품별 누적 다운로드 수를 사전수집/사후수집 기준으로 확인할 수 있습니다.
        - **팬덤의존도 확인 기능 추가**: 사전호응률, 초기호응률, 최종호응률, 팬덤의존도, 결제언급률을 회차별로 확인할 수 있습니다.
        - **전체 연재 웹툰 다운로드 수 수집**: 네이버웹툰 요일별 목록에 노출되는 전체 연재 작품을 대상으로 다운로드 수를 수집합니다.
        - **주간 매핑 업데이트**: 매주 1회 네이버웹툰 요일별 목록을 다시 확인해 신규 웹툰을 다운로드 수 수집 대상에 반영합니다.
        """
    )

    st.markdown("### 화면 구성")
    st.markdown(
        """
        - **데이터 개요**: 수집된 댓글/회차정보/웹툰 목록 요약
        - **단일 작품 모니터링**: 회차 흐름, 공개 후 누적 댓글, 작성자 팬층, 베스트 댓글 원문, 팬덤의존도, 다운로드 수
        - **두 웹툰 비교**: 회차별 지표 비교, 구간 병치, 구간 평균, 다운로드 수 비교, 베스트 댓글, 작성자 팬층
        """
    )


# ════════════════════════════════════════════════════════════
# 화면 1: 데이터 개요
# ════════════════════════════════════════════════════════════

elif mode == "데이터 개요":
    st.title("웹툰 반응 비교 대시보드")
    st.caption("수집된 웹툰 댓글, 회차정보, 태그 정보를 기준으로 단일 작품 모니터링과 두 작품 비교를 수행합니다.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("웹툰 수", f"{webtoon_meta['display_name'].nunique():,}")
    c2.metric("댓글 수", f"{len(comments_all):,}")
    c3.metric("회차정보 수", f"{len(episodes_all):,}")
    c4.metric("분석 회차 수", f"{summary_all[['title_name','episode_no']].drop_duplicates().shape[0]:,}" if not summary_all.empty else "0")

    st.markdown("#### 수집된 웹툰 목록")
    overview = webtoon_meta.copy()
    if not summary_all.empty:
        stats = summary_all.groupby(["title_id", "title_name"], dropna=False).agg(
            분석회차수=("episode_no", "nunique"),
            수집댓글수=("scraped", "sum"),
            유료댓글수=("paid", "sum"),
            베스트댓글수=("best_count", "sum"),
            평균별점=("rating", "mean"),
            평균별점참여=("rating_count", "mean"),
            평균좋아요=("episode_like_count", "mean"),
        ).reset_index()
        overview = overview.merge(stats, on=["title_id", "title_name"], how="left")
    display_cols = ["display_name", "episode_count", "genre", "tags", "분석회차수", "수집댓글수", "유료댓글수", "베스트댓글수", "평균별점", "평균별점참여", "평균좋아요"]
    display_cols = [c for c in display_cols if c in overview.columns]
    st.dataframe(overview[display_cols], use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════
# 화면 2: 단일 작품 모니터링
# ════════════════════════════════════════════════════════════

elif mode == "단일 작품 모니터링":
    st.title("단일 작품 모니터링")
    title_sel = st.selectbox("웹툰 선택", get_title_options(), key="single_title")
    one_summary = filter_by_title(summary_all, title_sel, webtoon_meta)
    one_comments = filter_by_title(comments_all, title_sel, webtoon_meta)
    eps = sorted(one_summary["episode_no"].dropna().astype(int).unique().tolist())
    seg, custom = segment_controls("single", eps)
    one_seg = filter_summary_segment(one_summary, seg, custom)
    one_comments_seg = filter_comments_segment(one_comments, one_seg)

    st.subheader(f"{title_sel} — {seg}")
    show_summary_cards(metric_summary(one_seg, one_comments_seg))
    st.divider()

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs(["회차 흐름", "구간 요약", "구간 병치 라인차트", "공개 후 누적 댓글", "전체 지표 통합", "작성자 팬층", "베스트 댓글 원문", "팬덤의존도", "다운로드 수", "상세 테이블"])
    with tab1:
        plot_single_episode_metrics(one_seg, title_sel)
        plot_single_comments(one_seg, title_sel)
    with tab2:
        st.markdown("#### 초기/중간/최근 구간 요약")
        rows = []
        for s in ["초기 20%", "중간 60%", "최근 20%", "전체"]:
            d = filter_summary_segment(one_summary, s, None)
            r = prepare_segment_summary_rows([(s, d)])
            if not r.empty:
                row = r.iloc[0].to_dict()
                row["구간"] = s
                rows.append(row)
        if rows:
            seg_tbl = pd.DataFrame(rows)
            show_cols = ["구간", "회차 수", "댓글 합계", "유료 댓글 비율(%)", "avg_comments", "avg_rating", "avg_rating_count", "avg_like", "avg_best", "베스트 중 유료 비율(%)"]
            st.dataframe(seg_tbl[show_cols], use_container_width=True, hide_index=True)
            fig = go.Figure()
            for col, label in [("avg_comments", "회차당 평균 댓글"), ("avg_rating_count", "평균 별점 참여"), ("avg_like", "평균 좋아요")]:
                fig.add_trace(go.Bar(x=seg_tbl["구간"], y=seg_tbl[col], name=label))
            base_layout(fig, f"{title_sel} 초기/중간/최근 구간 비교", "값", x_title="구간", height=430)
            fig.update_layout(barmode="group")
            render_plotly_chart(fig, use_container_width=True)
    with tab3:
        plot_segment_overlap_single(one_summary, title_sel, key_prefix="single")
    with tab4:
        draw_cumulative_comment_section(title_sel, one_comments_seg, key_prefix="single_cum")
    with tab5:
        draw_all_metric_single_chart(one_seg, title_sel, key_prefix="single_all")
    with tab6:
        show_author_section(one_comments_seg, title_sel)
    with tab7:
        st.markdown("#### 베스트 댓글 원문")
        show_best_comment_table(one_comments_seg, "single_best_inline")
    with tab8:
        ws_show_fandom_section(title_sel, one_comments, filter_by_title(episodes_all, title_sel, webtoon_meta), key_prefix="single_fandom")
    with tab9:
        ws_show_download_section(title_sel, webtoon_meta, key_prefix="single_download")
    with tab10:
        st.markdown("#### 회차별 상세 데이터")
        cols = ["episode_no", "episode_title", "uploaded_at", "rating", "rating_count", "episode_like_count", "author_per_rating", "scraped", "paid", "free", "paid_ratio", "best_count", "best_paid", "best_free", "best_paid_ratio", "unique_authors"]
        cols = [c for c in cols if c in one_seg.columns]
        safe_dataframe(one_seg[cols], use_container_width=True, hide_index=True)
        st.download_button("CSV 다운로드", one_seg[cols].to_csv(index=False, encoding="utf-8-sig"), file_name=f"{title_sel}_{seg}_회차요약.csv", mime="text/csv")


# ════════════════════════════════════════════════════════════
# 화면 3: 두 웹툰 비교
# ════════════════════════════════════════════════════════════

elif mode == "두 웹툰 비교":
    st.title("두 웹툰 비교")
    st.caption("장르/태그와 공개 회차 수로 후보를 좁힌 뒤, 사용자가 직접 두 웹툰을 선택합니다.")

    st.markdown("#### 비교 후보 좁히기")
    st.caption("태그/장르와 공개 회차 수 구간을 여러 개 선택해 후보군을 좁힌 뒤, 웹툰 A와 B를 직접 선택합니다.")
    all_tags = sorted(set(t for tags in webtoon_meta["tags"].fillna("").map(split_tags) for t in tags) | set(t for tags in webtoon_meta["genre"].fillna("").map(split_tags) for t in tags))
    selected_tags = multi_choice_click(
        "태그/장르 필터, 여러 개 선택 가능",
        all_tags,
        key="compare_tag_filter",
        default=[],
        help="선택한 태그나 장르가 하나라도 포함된 웹툰만 후보로 표시합니다. Streamlit 버전이 지원하면 클릭형 pill로 표시됩니다.",
    )
    max_count = int(pd.to_numeric(webtoon_meta["episode_count"], errors="coerce").max()) if webtoon_meta["episode_count"].notna().any() else 200
    band_options = [f"{i}~{i+10}화 이하" for i in range(0, max(10, ((max_count + 9) // 10) * 10), 10)]
    selected_bands = multi_choice_click(
        "공개 회차 수 구간, 여러 개 선택 가능",
        band_options,
        key="compare_episode_bands",
        default=[],
        help="예: 80~90화 이하, 90~100화 이하를 함께 선택할 수 있습니다. 아무것도 선택하지 않으면 전체를 봅니다.",
    )
    cand = webtoon_meta.copy()
    if selected_tags:
        selected_tag_set = set(selected_tags)
        cand = cand[cand.apply(lambda r: bool(selected_tag_set & set(split_tags(r.get("tags", "")) + split_tags(r.get("genre", "")))), axis=1)]
    if selected_bands:
        mask = pd.Series(False, index=cand.index)
        for band in selected_bands:
            nums = [int(x) for x in re.findall(r"\d+", band)[:2]]
            if len(nums) >= 2:
                a, b = nums
                mask = mask | ((cand["episode_count"].fillna(0) > a) & (cand["episode_count"].fillna(0) <= b))
        cand = cand[mask]
    st.caption(f"현재 후보 웹툰 {len(cand)}개")
    with st.expander("후보군 보기", expanded=False):
        show_cols = ["display_name", "episode_count", "genre", "tags"]
        show_cols = [c for c in show_cols if c in cand.columns]
        st.dataframe(cand[show_cols], use_container_width=True, hide_index=True)
    opts = get_title_options(cand)
    if len(opts) < 2:
        st.warning("필터 조건에 맞는 웹툰이 2개 미만입니다. 필터를 완화해주세요.")
        st.stop()

    csel1, csel2 = st.columns(2)
    n1 = csel1.selectbox("웹툰 A", opts, index=0, key="compare_title1")
    default_idx2 = 1 if len(opts) > 1 else 0
    n2 = csel2.selectbox("웹툰 B", opts, index=default_idx2, key="compare_title2")
    if n1 == n2:
        st.warning("서로 다른 두 웹툰을 선택해주세요.")
        st.stop()

    df1_all = filter_by_title(summary_all, n1, webtoon_meta)
    df2_all = filter_by_title(summary_all, n2, webtoon_meta)
    raw1_all = filter_by_title(comments_all, n1, webtoon_meta)
    raw2_all = filter_by_title(comments_all, n2, webtoon_meta)

    all_eps = sorted(set(df1_all["episode_no"].dropna().astype(int).tolist() + df2_all["episode_no"].dropna().astype(int).tolist()))
    seg, custom = segment_controls("compare", all_eps)
    df1 = filter_summary_segment(df1_all, seg, custom)
    df2 = filter_summary_segment(df2_all, seg, custom)
    raw1 = filter_comments_segment(raw1_all, df1)
    raw2 = filter_comments_segment(raw2_all, df2)

    st.subheader(f"{n1} vs {n2} — {seg}")
    tag1, tag2 = set(common_tags_for_title(n1)), set(common_tags_for_title(n2))
    common = sorted(tag1 & tag2)
    if common:
        st.caption("공통 태그/장르: " + ", ".join(common[:20]))

    left, right = st.columns(2)
    with left:
        st.markdown(f"##### {n1}")
        show_summary_cards(metric_summary(df1, raw1))
    with right:
        st.markdown(f"##### {n2}")
        show_summary_cards(metric_summary(df2, raw2))

    st.divider()
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(["회차별 비교", "구간 병치 라인차트", "구간 평균 비교", "다운로드 수 비교", "베스트 댓글", "작성자 팬층", "상세 테이블"])
    with tab1:
        plot_compare_core_metrics(df1, df2, n1, n2)
        plot_compare_comments(df1, df2, n1, n2)
    with tab2:
        plot_segment_overlap_compare(df1_all, df2_all, n1, n2, key_prefix="compare")
    with tab3:
        st.markdown("#### 선택 구간 요약 비교")
        seg_rows = prepare_segment_summary_rows([(n1, df1), (n2, df2)])
        if not seg_rows.empty:
            display = seg_rows[["작품", "회차 수", "댓글 합계", "유료 댓글 합계", "일반 댓글 합계", "유료 댓글 비율(%)", "avg_comments", "avg_rating", "avg_rating_count", "avg_like", "avg_best", "베스트 중 유료 비율(%)"]].copy()
            display.columns = ["작품", "회차 수", "댓글 합계", "유료 댓글 합계", "일반 댓글 합계", "유료 댓글 비율(%)", "회차당 평균 댓글", "평균 별점", "평균 별점 참여", "평균 좋아요", "회차당 평균 베스트", "베스트 중 유료 비율(%)"]
            st.dataframe(display, use_container_width=True, hide_index=True)
            plot_segment_bar(seg_rows)
    with tab4:
        ws_show_download_compare_section(n1, n2, webtoon_meta, key_prefix="compare_download")
    with tab5:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"##### {n1}")
            show_best_comment_table(raw1, "cmp_best_1")
        with col2:
            st.markdown(f"##### {n2}")
            show_best_comment_table(raw2, "cmp_best_2")
    with tab6:
        col1, col2 = st.columns(2)
        with col1:
            show_author_section(raw1, n1)
        with col2:
            show_author_section(raw2, n2)
    with tab7:
        cols = ["episode_no", "episode_title", "uploaded_at", "rating", "rating_count", "episode_like_count", "author_per_rating", "scraped", "paid", "free", "paid_ratio", "best_count", "best_paid", "best_free", "best_paid_ratio", "unique_authors"]
        cols1 = [c for c in cols if c in df1.columns]
        cols2 = [c for c in cols if c in df2.columns]
        col1, col2 = st.columns(2)
        col1.markdown(f"##### {n1}")
        with col1:
            safe_dataframe(df1[cols1], use_container_width=True, hide_index=True)
        col2.markdown(f"##### {n2}")
        with col2:
            safe_dataframe(df2[cols2], use_container_width=True, hide_index=True)




# ════════════════════════════════════════════════════════════
# 화면 4: 베스트 댓글 원문
# ════════════════════════════════════════════════════════════

elif mode == "베스트 댓글 원문":
    st.title("베스트 댓글 원문 보기")
    title_sel = st.selectbox("웹툰 선택", ["전체"] + get_title_options(), key="best_title_filter")
    data = comments_all.copy() if title_sel == "전체" else filter_by_title(comments_all, title_sel, webtoon_meta)
    if not data.empty:
        eps = sorted(data["episode_no"].dropna().astype(int).unique().tolist())
        if eps:
            ep_range = st.slider("회차 범위", min(eps), max(eps), (min(eps), max(eps)), key="best_ep_range")
            data = data[(data["episode_no"] >= ep_range[0]) & (data["episode_no"] <= ep_range[1])]
    show_best_comment_table(data, "best_page")

