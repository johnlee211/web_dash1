import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from streamlit_searchbox import st_searchbox

st.set_page_config(
    page_title="네이버 웹툰 반응 분석 대시보드",
    layout="wide"
)

st.title("네이버 웹툰 반응 분석 대시보드")

# =========================
# 1. 데이터 불러오기
# =========================

@st.cache_data
def load_data():
    df = pd.read_csv("data/analysis_df.csv")
    return df

analysis_df = load_data()

rank_section_order = [
    "최상위 10%",
    "상위 10~30%",
    "중위 30~70%",
    "하위 70~90%",
    "최하위 10%"
]

metrics = [
    "별점",
    "별점 참여 수",
    "댓글",
    "관심수대비별점",
    "관심수대비별점참여자수",
    "관심수대비댓글수"
]

analysis_df["순위구간"] = pd.Categorical(
    analysis_df["순위구간"],
    categories=rank_section_order,
    ordered=True
)

# =========================
# 2. 공통 함수
# =========================

def get_metric_cols(metric):
    return (
        f"{metric}_초반평균",
        f"{metric}_후반평균",
        f"{metric}_하락폭_pct"
    )


def format_value(value):
    if pd.isna(value):
        return "-"
    return f"{value:,.2f}"


def format_pct(value):
    if pd.isna(value):
        return "-"
    return f"{value:+.2f}%"


def make_trend_chart(target_name, metric, target_values, mean_values, median_values, section):
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=["초반", "후반"],
        y=target_values,
        mode="lines+markers+text",
        name=target_name,
        text=[format_value(v) for v in target_values],
        textposition="top center",
        line=dict(width=4)
    ))

    fig.add_trace(go.Scatter(
        x=["초반", "후반"],
        y=mean_values,
        mode="lines+markers+text",
        name=f"{section} 평균",
        text=[format_value(v) for v in mean_values],
        textposition="bottom center",
        line=dict(width=3, dash="dash")
    ))

    fig.add_trace(go.Scatter(
        x=["초반", "후반"],
        y=median_values,
        mode="lines+markers+text",
        name=f"{section} 중앙값",
        text=[format_value(v) for v in median_values],
        textposition="bottom right",
        line=dict(width=3, dash="dot")
    ))

    fig.update_layout(
        title=f"{metric} 초반, 후반 추세 비교",
        xaxis_title="구간",
        yaxis_title=metric,
        template="plotly_white",
        height=480,
        legend=dict(orientation="h", yanchor="bottom", y=1.02)
    )

    return fig


def make_change_chart(metric, target_change, section_mean_change, section_median_change):
    fig = go.Figure()

    colors = []
    for value in [target_change, section_mean_change, section_median_change]:
        if value >= 0:
            colors.append("#2ca02c")  # 증가
        else:
            colors.append("#d62728")  # 감소

    fig.add_trace(go.Bar(
        x=["선택 작품", "순위권 평균", "순위권 중앙값"],
        y=[target_change, section_mean_change, section_median_change],
        text=[
            format_pct(target_change),
            format_pct(section_mean_change),
            format_pct(section_median_change)
        ],
        textposition="outside",
        marker_color=colors
    ))

    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="gray"
    )

    fig.update_layout(
        title=f"{metric} 초반 대비 후반 증감률 비교",
        yaxis_title="증감률(%)",
        template="plotly_white",
        height=420
    )

    return fig


def make_section_summary_chart(section_summary, metric):
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=["초반 평균", "후반 평균"],
        y=[
            section_summary.loc["초반 평균", metric],
            section_summary.loc["후반 평균", metric]
        ],
        name="평균",
        text=[
            format_value(section_summary.loc["초반 평균", metric]),
            format_value(section_summary.loc["후반 평균", metric])
        ],
        textposition="outside"
    ))

    fig.add_trace(go.Bar(
        x=["초반 중앙값", "후반 중앙값"],
        y=[
            section_summary.loc["초반 중앙값", metric],
            section_summary.loc["후반 중앙값", metric]
        ],
        name="중앙값",
        text=[
            format_value(section_summary.loc["초반 중앙값", metric]),
            format_value(section_summary.loc["후반 중앙값", metric])
        ],
        textposition="outside"
    ))

    fig.update_layout(
        title=f"{metric} 순위권 초반, 후반 요약",
        yaxis_title=metric,
        barmode="group",
        template="plotly_white",
        height=420
    )

    return fig


def make_section_change_chart(metric, mean_change, median_change):
    colors = [
        "#2ca02c" if mean_change >= 0 else "#d62728",
        "#2ca02c" if median_change >= 0 else "#d62728"
    ]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=["순위권 평균 증감률", "순위권 중앙값 증감률"],
        y=[mean_change, median_change],
        text=[format_pct(mean_change), format_pct(median_change)],
        textposition="outside",
        marker_color=colors
    ))

    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="gray"
    )

    fig.update_layout(
        title=f"{metric} 순위권 초반 대비 후반 증감률",
        yaxis_title="증감률(%)",
        template="plotly_white",
        height=420
    )

    return fig


def get_change_comment(webtoon_name, metric, target_change, section_mean_change, section_median_change):
    if target_change > section_mean_change:
        mean_comment = (
            f"{webtoon_name}의 {metric} 증감률은 동일 순위권 평균보다 높습니다. "
            "즉, 같은 순위권 작품들에 비해 반응을 상대적으로 잘 유지하거나 더 크게 증가한 편입니다."
        )
    elif target_change < section_mean_change:
        mean_comment = (
            f"{webtoon_name}의 {metric} 증감률은 동일 순위권 평균보다 낮습니다. "
            "즉, 같은 순위권 작품들에 비해 반응 감소가 더 큰 편입니다."
        )
    else:
        mean_comment = (
            f"{webtoon_name}의 {metric} 증감률은 동일 순위권 평균과 유사합니다."
        )

    if target_change >= 0:
        change_comment = (
            f"선택 작품은 초반 대비 후반에 {metric}이 {abs(target_change):.2f}% 증가했습니다."
        )
    else:
        change_comment = (
            f"선택 작품은 초반 대비 후반에 {metric}이 {abs(target_change):.2f}% 감소했습니다."
        )

    return change_comment + " " + mean_comment


# =========================
# 3. 페이지 구성
# =========================

page = st.sidebar.radio(
    "페이지 선택",
    ["작품 비교", "순위권 요약"]
)

# =========================
# 4. 작품 비교 페이지
# =========================

if page == "작품 비교":
    st.header("작품별 순위권 대비 성과 비교")

    col_filter1, col_filter2 = st.columns([1, 1])

    with col_filter1:
        selected_section = st.selectbox(
            "순위구간 선택",
            rank_section_order
        )

    with col_filter2:
        selected_metric = st.selectbox(
            "지표 선택",
            metrics,
            index=2
        )

    section_df = analysis_df[analysis_df["순위구간"] == selected_section].copy()
    section_df = section_df.sort_values("평균순위")

    webtoon_options = (
        section_df["웹툰 명"]
        .dropna()
        .astype(str)
        .sort_values()
        .unique()
        .tolist()
    )

    def search_webtoon(searchterm: str):
        if not searchterm:
            return webtoon_options[:20]

        searchterm = searchterm.lower().strip()

        results = [
            name for name in webtoon_options
            if searchterm in name.lower()
        ]

        return results[:20]

    selected_webtoon = st_searchbox(
        search_function=search_webtoon,
        placeholder="작품명을 입력하세요",
        label="작품명 검색",
        key=f"webtoon_search_{selected_section}"
    )

    if selected_webtoon is None:
        st.info("작품명을 검색한 뒤 자동완성 목록에서 작품을 선택해주세요.")
        st.stop()

    target_df = section_df[section_df["웹툰 명"].astype(str) == selected_webtoon]

    if len(target_df) == 0:
        st.warning("선택한 작품을 찾을 수 없습니다.")
        st.stop()

    target = target_df.iloc[0]

    early_col, late_col, drop_col = get_metric_cols(selected_metric)

    valid_section_df = section_df.replace([np.inf, -np.inf], np.nan).dropna(
        subset=[early_col, late_col, drop_col]
    )

    target_early = target[early_col]
    target_late = target[late_col]
    target_drop = target[drop_col]

    section_early_mean = valid_section_df[early_col].mean()
    section_late_mean = valid_section_df[late_col].mean()
    section_drop_mean = valid_section_df[drop_col].mean()

    section_early_median = valid_section_df[early_col].median()
    section_late_median = valid_section_df[late_col].median()
    section_drop_median = valid_section_df[drop_col].median()

    # 기존 하락폭 컬럼을 화면 표시용 증감률로 변환
    target_change = -target_drop
    section_mean_change = -section_drop_mean
    section_median_change = -section_drop_median

    st.subheader(f"{selected_webtoon} 분석 결과")

    info_cols = st.columns(5)

    info_cols[0].metric("순위구간", selected_section)
    info_cols[1].metric("요일", str(target.get("요일", "-")))
    info_cols[2].metric("평균순위", format_value(target.get("평균순위", np.nan)))
    info_cols[3].metric("대표장르", str(target.get("대표장르", "-")))
    info_cols[4].metric("분석회차수", int(target.get("분석회차수", 0)))

    kpi_cols = st.columns(3)

    kpi_cols[0].metric("선택 작품 증감률", format_pct(target_change))
    kpi_cols[1].metric("동일 순위권 평균 증감률", format_pct(section_mean_change))
    kpi_cols[2].metric("동일 순위권 중앙값 증감률", format_pct(section_median_change))

    comment = get_change_comment(
        selected_webtoon,
        selected_metric,
        target_change,
        section_mean_change,
        section_median_change
    )

    if target_change >= section_mean_change:
        st.success(comment)
    else:
        st.warning(comment)

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        fig_trend = make_trend_chart(
            target_name=selected_webtoon,
            metric=selected_metric,
            target_values=[target_early, target_late],
            mean_values=[section_early_mean, section_late_mean],
            median_values=[section_early_median, section_late_median],
            section=selected_section
        )
        st.plotly_chart(fig_trend, use_container_width=True)

    with chart_col2:
        fig_change = make_change_chart(
            metric=selected_metric,
            target_change=target_change,
            section_mean_change=section_mean_change,
            section_median_change=section_median_change
        )
        st.plotly_chart(fig_change, use_container_width=True)

    compare_table = pd.DataFrame({
        "구분": ["선택 작품", "순위권 평균", "순위권 중앙값"],
        "초반 값": [target_early, section_early_mean, section_early_median],
        "후반 값": [target_late, section_late_mean, section_late_median],
        "초반 대비 후반 증감률(%)": [
            target_change,
            section_mean_change,
            section_median_change
        ]
    })

    st.subheader("비교 표")
    st.dataframe(compare_table.round(3), use_container_width=True)

    st.subheader("해석 기준")
    st.markdown(
        """
        - 증감률은 `초반 대비 후반에 얼마나 증가 또는 감소했는지`를 나타냅니다.
        - 증감률이 `+`이면 후반 반응이 초반보다 증가한 것입니다.
        - 증감률이 `-`이면 후반 반응이 초반보다 감소한 것입니다.
        - 선택 작품의 증감률이 동일 순위권 평균보다 높으면, 같은 순위권 대비 반응을 더 잘 유지한 것으로 볼 수 있습니다.
        - 선택 작품의 증감률이 동일 순위권 평균보다 낮으면, 같은 순위권 대비 반응 감소가 더 큰 것으로 볼 수 있습니다.
        """
    )

# =========================
# 5. 순위권 요약 페이지
# =========================

elif page == "순위권 요약":
    st.header("순위권별 지표 요약")

    col1, col2 = st.columns(2)

    with col1:
        selected_section = st.selectbox(
            "순위구간 선택",
            rank_section_order
        )

    with col2:
        selected_metric = st.selectbox(
            "지표 선택",
            metrics,
            index=2
        )

    section_df = analysis_df[analysis_df["순위구간"] == selected_section].copy()
    section_df = section_df.replace([np.inf, -np.inf], np.nan)

    early_col, late_col, drop_col = get_metric_cols(selected_metric)

    section_drop_mean = section_df[drop_col].mean()
    section_drop_median = section_df[drop_col].median()

    section_mean_change = -section_drop_mean
    section_median_change = -section_drop_median

    summary_table = pd.DataFrame({
        "구분": [
            "초반 평균",
            "후반 평균",
            "초반 중앙값",
            "후반 중앙값",
            "평균 기준 증감률",
            "중앙값 기준 증감률"
        ],
        selected_metric: [
            section_df[early_col].mean(),
            section_df[late_col].mean(),
            section_df[early_col].median(),
            section_df[late_col].median(),
            section_mean_change,
            section_median_change
        ]
    }).set_index("구분")

    st.subheader(f"{selected_section} | {selected_metric} 요약")

    kpi_cols = st.columns(4)

    kpi_cols[0].metric("작품 수", len(section_df))
    kpi_cols[1].metric("초반 평균", format_value(summary_table.loc["초반 평균", selected_metric]))
    kpi_cols[2].metric("후반 평균", format_value(summary_table.loc["후반 평균", selected_metric]))
    kpi_cols[3].metric("평균 기준 증감률", format_pct(section_mean_change))

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        fig_summary = make_section_summary_chart(summary_table, selected_metric)
        st.plotly_chart(fig_summary, use_container_width=True)

    with chart_col2:
        fig_section_change = make_section_change_chart(
            selected_metric,
            section_mean_change,
            section_median_change
        )
        st.plotly_chart(fig_section_change, use_container_width=True)

    st.subheader("요약 표")
    st.dataframe(summary_table.round(3), use_container_width=True)

    st.subheader("순위권 내 작품별 증감률")

    drop_rank_df = (
        section_df[["웹툰 명", "요일", "평균순위", "대표장르", drop_col]]
        .dropna(subset=[drop_col])
        .copy()
    )

    drop_rank_df["초반 대비 후반 증감률(%)"] = -drop_rank_df[drop_col]

    drop_rank_df = (
        drop_rank_df
        .drop(columns=[drop_col])
        .sort_values("초반 대비 후반 증감률(%)", ascending=False)
    )

    st.dataframe(drop_rank_df.round(3), use_container_width=True)

    st.markdown(
        """
        - 증감률이 높은 작품은 해당 순위권 안에서 반응을 상대적으로 잘 유지했거나 증가한 작품입니다.
        - 증감률이 낮은 작품은 해당 순위권 안에서 반응 감소가 상대적으로 큰 작품입니다.
        - 증감률이 `+`이면 초반 대비 후반 반응이 증가한 것이고, `-`이면 감소한 것입니다.
        - 평균과 중앙값의 차이가 크다면, 일부 작품이 순위권 전체 평균에 큰 영향을 주고 있을 가능성이 있습니다.
        """
    )