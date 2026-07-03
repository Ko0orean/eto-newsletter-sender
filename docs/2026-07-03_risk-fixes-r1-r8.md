# 2026-07-03 (3차) 리스크 개선 적용: R1, R2, R3, R5, R6, R7, R8

`2026-07-03_multi-test-and-risk-review.md`에서 제안한 항목 중 사용자가 선택한
7건을 구현. (R4·R9·R10은 미적용 상태로 남음.)

## R1. 중복 발송 방지
- **발송 직전 가드**: 카운트다운 시작 전에 MailerLite에서 "같은 제목으로 최근
  24시간 내 발송된 캠페인"을 조회. 있으면 발송 시각과 함께 경고 다이얼로그
  (기본값 No). 타임스탬프를 파싱할 수 없는 캠페인은 안전한 방향(=최근으로 간주)
  으로 처리.
- **발송 후 재잠금**: 발송 성공 시 확인 체크박스가 자동 해제되어 재발송하려면
  다시 의도적으로 체크해야 함. 힌트에 "이미 발송됨 (campaign ID)" 표시.
- 구현: `service.find_recent_same_subject()`, `client.list_sent_campaigns()`,
  `app._after_duplicate_check()`, `_on_send_done()` 재잠금.

## R2. 테스트 후 내용 변경 감지
- 테스트 발송 성공 시 드래프트 지문(제목, MD 파일 경로, PDF 링크, **MD 파일
  mtime**)을 저장. 이후 제목/PDF 입력 변경, MD 파일 재선택 시 즉시 테스트 ✓가
  리셋되고, 발송 버튼 클릭 시에도 mtime을 재확인(디스크에서 파일만 수정한
  경우까지 잡음). 리셋되면 경고 후 발송 중단.
- 구현: `app._draft_fingerprint()`, `_invalidate_test_if_changed()`.

## R3. PDF 링크 사전 확인
- 테스트 발송과 실제 발송 모두, 캠페인 생성 전에 PDF URL에 HEAD 요청
  (HEAD 거부 서버는 GET으로 재시도, 본문은 다운로드하지 않음). HTTP 400 이상
  또는 무응답이면 발송 중단 + 명확한 에러.
- 구현: `client.check_url_ok()`, `service._check_pdf_link()` — `send_test`와
  `send_to_selected` 양쪽에서 호출.

## R5. CP949 CSV 지원
- CSV 로드가 utf-8-sig 디코드 실패 시 CP949로 자동 재시도. 한국어 Excel의
  기본 "CSV(쉼표로 분리)" 저장 파일이 바로 열림.
- 구현: `content.load_subscribers()` → `_load_subscribers(path, encoding)` 분리.

## R6. 대량 리스트 속도 / 레이트리밋
- 구독자 업서트를 **`/batch` 엔드포인트(호출당 50건)**로 전환 — 500명 기준
  500콜 → 10콜. 배치 실패(엔드포인트 불가·일부 거절) 시 해당 청크만 개별
  업서트로 폴백해서 정확한 문제 행을 드러냄.
- 429 재시도에 **최대 5회 상한** 추가(기존은 이론상 무한 재귀).
- 구현: `client.upsert_subscribers_bulk()`, `_request(_attempt)` 상한;
  `service.sync_contacts`/`_fresh_group`이 벌크 사용.

## R7. 탈퇴자 계정 정리(옵션)
- 비교 다이얼로그에 **"Delete N departed contact(s) from MailerLite…"** 버튼
  (삭제 가능 대상이 있을 때만 표시). 확인 다이얼로그(되돌릴 수 없음 경고) 후
  백그라운드로 삭제, 결과 요약 표시.
- **status가 active인 탈퇴자만 삭제** — unsubscribed/bounced는 opt-out 기록
  보존을 위해 의도적으로 남김(삭제하면 CSV에 재등장 시 다시 발송될 위험).
- 구현: `client.delete_subscriber()`, `service.delete_departed()`,
  `ComparisonDialog` 삭제 버튼 + `app._on_delete_departed_done()`.
- 참고: `GroupComparison.departed`가 (email, status) → (email, status, id)
  3-튜플로 변경됨.

## R8. [TEST] 캠페인 자동 정리
- 테스트 발송 시작 시, 이미 발송 완료된 `[TEST] `로 시작하는 캠페인을 모두
  삭제(best-effort — 실패해도 테스트 발송은 진행). 대시보드에 테스트 잔재가
  쌓이지 않음.
- 구현: `service._cleanup_test_campaigns()` (send_test 초입 호출),
  `client.delete_campaign()`.

## 검증
- `py_compile` + `import eto_newsletter.app` 통과.
- Fake client 유닛 테스트 전부 통과:
  - 비교 결과 3-튜플, delete_departed의 active-only 삭제/스킵 목록
  - 타임스탬프 파싱(공백형·ISO·불량), 중복 가드(24h 경계, 파싱 불가 → 경고)
  - 벌크 업서트 청크(50/50/20) 및 배치 실패 폴백(120건 개별)
  - PDF 링크 체크(200 통과, 404 중단, 링크 없음 스킵)
  - CP949 CSV 자동 인식(한글 이름 정상 파싱)

## 추가 수정 (같은 날, 스크린샷 피드백)

**"MailerLite에 다 있는데 전원 신규로 나옴" 문제.**
비교 기준이 저장 그룹(`Korea contacts`, 첫 실발송 후 생성)뿐이라, 대시보드로
미리 임포트해 둔 구독자가 있어도 그룹이 없으면 전원 신규로 표시됐음.

- 저장 그룹이 없으면 **계정 전체 구독자(모든 status)와 비교**하도록 폴백.
  다이얼로그·상태바에 어느 기준(last send / whole account)인지 명시.
- `find_group`을 대소문자·공백 무시 매칭으로 완화(대시보드에서 이름이 살짝
  바뀌어도 찾음).
- `GroupComparison.group_exists/group_total` → `basis`("group"/"account"/"none")
  / `baseline_total`로 교체. 페이지네이션은 `_list_paginated()`로 공통화,
  `list_all_subscribers()` 추가.
- 유닛 테스트: account 폴백, 빈 계정, 그룹 우선, 관대한 그룹 매칭 통과.

## UI 다듬기 (같은 날)

- Before-sending 체크리스트의 "1. / 2." 번호 표기 제거 — 두 작업(대조, 테스트)
  은 원래 로직상 순서 무관이었으나 번호 때문에 순서가 있는 것처럼 보였음.
  헤더를 "Before sending (both required, in any order)"로 변경, 잠금 툴팁에도
  "(in any order)" 명시.

## 남은 제안 (미적용)
- R4: suspicious 행 기본 해제
- R9: 프록시 루트 CA 적용 + API 키 DPAPI 보관 (IT 협조 필요)
- R10: 확인 다이얼로그에 수신거부 자동 제외 인원 표기
