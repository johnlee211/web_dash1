# Webtoon Series Auto Code Patch v10 - Syntax and Increase Dedupe Fixed

## v4 핵심 수정

이전 주간 업데이트 문제:

```text
webtoon_rows = 732
mapping_rows = 47
adult_rows = 685
matched_rows = 0
```

원인:

- 작가 확인 단계에서 일반 웹툰을 adult/login required로 과도하게 분류
- 기존 productNo 매핑을 유지하지 않고, 일부 재검색 대상만 최종 mapping에 남김

v4 수정:

- 기존 `data/naver_webtoon_series_productNo_mapping_v2_title_cleaned.csv`를 먼저 로드
- 최신 네이버웹툰 목록 732개를 다시 수집
- 기존 productNo가 있는 작품은 그대로 재사용
- 신규/미매칭/productNo 공란 작품만 네이버 시리즈 재검색
- 최종 `weekly_mapping`과 mapping csv에는 전체 현재 웹툰 목록을 유지
- adult/login 작품은 별도 탭으로 분류하되, 최종 mapping에서 제거하지 않음
- 기존 mapping은 `naver_webtoon_series_productNo_mapping_previous.csv/xlsx`로 백업

## 자동화

### 매일 다운로드 수 수집

```text
.github/workflows/daily_download_snapshot.yml
scripts/collect_downloads.py
```

매일 21:30 KST 실행.

### 주간 웹툰 목록/productNo 매핑 갱신

```text
.github/workflows/weekly_mapping_update.yml
scripts/update_mapping.py
```

매주 월요일 09:00 KST 실행.

## GitHub Secrets

```text
GOOGLE_SHEETS_ID
GOOGLE_SERVICE_ACCOUNT_JSON
```

## Google Sheets 탭

```text
snapshot_history
increase_report
dedup_mapping
problem_rows
weekly_mapping_raw
weekly_mapping
adult_login_required
mapping_duplicate_rows
weekly_recheck_targets
mapping_update_log
```

## 확인 기준

주간 업데이트 완료 후 `mapping_update_log`에서 아래가 정상에 가까워야 합니다.

```text
mapping_rows ≈ webtoon_rows
adult_rows가 비정상적으로 수백 개면 안 됨
product_no_blank_rows가 크게 늘면 안 됨
```

## 강제 전체 재검색

기본은 증분 갱신입니다.

전체 재검색이 필요하면 workflow env를 아래처럼 바꾸세요.

```yaml
FORCE_RECHECK_ALL: "1"
```


## v5 수정: mapping_update_log 헤더 자동 정리

이전 실행에서 `mapping_update_log` 탭의 헤더가 예전 버전으로 남아 있으면
새 로그 값이 잘못된 컬럼 아래로 밀릴 수 있었습니다.

v5에서는 주간 업데이트 실행 시:

```text
현재 로그 컬럼과 기존 mapping_update_log 헤더 비교
→ 다르면 mapping_update_log 탭 초기화
→ 새 헤더로 다시 생성
→ 이번 실행 로그 append
```

방식으로 정리합니다.

따라서 다음 주간 업데이트 실행 후 `mapping_update_log` 탭은 새 헤더 기준으로 정상 표시됩니다.


## v6 수정: mapping_update_log 강제 초기화

v5의 헤더 비교 방식이 구글시트에서 기대대로 반영되지 않을 수 있어,
v6에서는 더 확실한 방식으로 바꿨습니다.

주간 업데이트가 끝날 때마다:

```text
mapping_update_log 탭
→ 무조건 전체 삭제
→ 현재 코드의 최신 헤더 작성
→ 이번 실행 로그 1행만 기록
```

따라서 기존 구버전 헤더 때문에 값이 밀려 보이는 문제가 사라집니다.

누적 로그가 필요할 수 있으므로 아래 탭도 추가했습니다.

```text
mapping_update_log_history
```

이 탭은 같은 헤더일 때만 누적 append하고,
헤더가 바뀌면 초기화 후 새 헤더로 다시 시작합니다.

## 교체 필수 파일

```text
scripts/update_mapping.py
```

전체 패키지를 덮어씌워도 되고, 단독 파일을 `scripts/update_mapping.py`로 교체해도 됩니다.


## v7 수정: GitHub Actions pull/rebase 충돌 방지

이전 에러:

```text
error: The following untracked working tree files would be overwritten by merge
```

원인:

```text
scripts/update_mapping.py 실행
→ data/에 새 파일 생성
→ 그 다음 git pull 실행
→ 원격 main에도 같은 이름의 파일 존재
→ untracked file 충돌
```

v7에서는 workflow 순서를 수정했습니다.

```text
checkout
→ 먼저 git pull --rebase origin main
→ 스크립트 실행
→ git add data/
→ git commit
→ git pull --rebase origin main
→ git push
```

수정된 파일:

```text
.github/workflows/daily_download_snapshot.yml
.github/workflows/weekly_mapping_update.yml
```


## v8 수정: Daily increase_report 헤더/증가량 리포트 정리

확인된 문제:

```text
snapshot_history 적재와 다운로드 수 수집은 정상
increase_report 탭은 구버전 헤더가 남아 새 컬럼 값이 오른쪽으로 밀림
2026-06-18 실행분은 당시 GitHub data history가 충분히 이어지지 않아 baseline_found=False
```

v8 수정:

```text
1. increase_report는 더 이상 append하지 않고 전체 snapshot_history 기준 재계산본으로 매번 교체
2. baseline 컬럼 스키마 고정
   - baseline_found
   - baseline_snapshot_id
   - baseline_collected_at_kst
   - baseline_download_text
   - baseline_download_count
   - download_increase
   - download_increase_rate
3. baseline 매칭은 release_event_key 우선 사용
4. snapshot_history/problem_rows append 시 기존 헤더와 신규 컬럼이 달라도 값이 밀리지 않게 헤더 확장 후 append
5. 현재 구글시트만 즉시 고치기 위한 repair workflow 추가
```

수정된 파일:

```text
scripts/collect_downloads.py
scripts/repair_increase_report_from_sheet.py
.github/workflows/repair_increase_report.yml
```

즉시 구글시트의 `increase_report` 탭만 복구하려면 GitHub Actions에서 아래 workflow를 수동 실행하세요.

```text
Repair Increase Report From Snapshot History
```

다음 daily 실행부터는 `increase_report` 탭이 자동으로 올바른 헤더와 값으로 교체됩니다.


## v9 주의: data 폴더 제외 패치

이 패키지는 코드/워크플로우만 교체하도록 만든 패치입니다.

일부 환경에서 전체 패키지를 그대로 덮어씌우면 `data/`의 누적 수집 파일이 예전 파일로 되돌아갈 수 있습니다.
그래서 v9 패키지에서는 `data/` 폴더를 아예 제외했습니다.

덮어씌울 파일:

```text
scripts/collect_downloads.py
scripts/update_mapping.py
scripts/repair_increase_report_from_sheet.py
.github/workflows/daily_download_snapshot.yml
.github/workflows/weekly_mapping_update.yml
.github/workflows/repair_increase_report.yml
requirements.txt
README.md
```

덮어씌우면 안 되는 것:

```text
data/
```

적용 후 실행 순서:

```text
1. Repair Increase Report From Snapshot History
2. Daily Naver Series Download Snapshot
```

확인 기준:

```text
increase_report 탭에 Unnamed 컬럼이 없어야 함
baseline_found가 True인 행이 생겨야 함
snapshot_history에 최신 snapshot_id가 추가되어야 함
mapping_update_log가 최신 헤더 1행 구조여야 함
```


## v10 수정: collect_downloads 문법 오류 및 increase_report 중복 방지

이전 패치 파일의 `scripts/collect_downloads.py`에 일부 함수명이 중복으로 붙은 문제가 있었습니다.

예:

```text
def replace_df_to_sheetdef replace_df_to_sheet(...)
def now_kstdef now_kst()
def main():def main():
```

v10에서 이 문법 오류를 수정했고 `python -m py_compile`로 확인했습니다.

또한 Daily를 같은 날 수동으로 여러 번 실행하면 같은 작품/요일의 `post_24h`가 여러 번 `increase_report`에 들어갈 수 있었습니다.
이 경우 대시보드에서 주간 증가량이 중복 합산될 수 있으므로, v10에서는 다음 기준으로 중복을 제거합니다.

```text
같은 post 수집일 + 같은 release_event_key
→ 가장 늦게 수집된 post_24h 1건만 유지
```

적용 후 먼저 실행할 workflow:

```text
Repair Increase Report From Snapshot History
```

그 다음 필요하면 Daily를 1회 수동 실행하면 됩니다.
