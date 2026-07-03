# 2026-07-03 기능 추가: 그룹 대조 + 발송 게이트(5초 카운트다운)

## 요청 사항

1. MailerLite에 등록된 Subscriber 그룹을 불러와서, 새 뉴스레터 발송 전 새 CSV 리스트와
   대조 → 신규유입 / 탈퇴 비교.
2. 테스트 메일 발송 + 확인 버튼을 눌러야만 실제 발송 가능. 발송 직전 5초 카운트다운
   (그동안 취소 가능) 후 전송.

## 구현 내용

### 1. 그룹 대조 (Compare with MailerLite)

- 툴바에 **Compare with MailerLite** 버튼 추가 (CSV 업로드 후 활성화).
- 기준 그룹은 `Korea contacts` (service.py의 `MAIN_GROUP`).
  - **실제 발송이 성공할 때마다 이 그룹을 그 달의 발송 명단으로 재생성**하므로,
    항상 "지난 호를 받은 사람들"과 비교하게 됨 (`send_to_selected` 마지막 단계).
  - 첫 발송 전에는 그룹이 없으므로 "전원 신규"로 안내.
- 비교 결과 3분류 (다이얼로그로 표시, 상태바에도 요약):
  - **New sign-ups** — CSV에는 있으나 저장된 그룹에는 없음 (신규유입)
  - **Departed** — 그룹에는 있으나 CSV에서 빠짐 (탈퇴, MailerLite 상태 함께 표시)
  - **Unsubscribed via MailerLite** — CSV에도 있지만 수신거부 링크로 탈퇴한 사람
    (MailerLite가 자동으로 발송 제외함)
- 이메일 비교는 대소문자 무시.

### 2. 발송 게이트 + 5초 카운트다운

Campaign 패널에 **Before sending 체크리스트** 추가:

1. `○/✓ 1. Compare the list with MailerLite`
2. `○/✓ 2. Send yourself a test email`
3. 두 단계가 모두 끝나야 **"I reviewed the comparison and the test email"**
   체크박스가 활성화되고, 이 체크박스를 켜야 발송 버튼이 활성화됨.
4. CSV를 새로 업로드하면 체크리스트가 전부 초기화됨 (새 리스트 = 새 검증).

발송 순서 (기존 2단계 유지 + 카운트다운 추가):

1. 1차 클릭 → 수신자 체크박스 검토 (기존 동일)
2. 2차 클릭 → 확인 다이얼로그 (기존 동일)
3. **NEW:** Yes를 눌러도 즉시 발송하지 않고 5초 카운트다운 시작.
   버튼이 빨간색 `CANCEL — sending in N s` 로 바뀌며, 클릭하면 취소
   (아무것도 발송되지 않음, armed 상태는 유지되어 재시도 가능).
4. 5초 경과 시 실제 전송 (백그라운드 스레드, 기존 안전장치
   `_verify_targeting` 그대로 통과).

카운트다운 중에는 테스트/비교 버튼이 잠기고, 창을 닫으면 카운트다운이 자동
취소됨(발송 안 됨).

## 변경 파일

| 파일 | 변경 |
|---|---|
| `eto_newsletter/mailerlite_client.py` | `list_group_subscribers()` 추가 — 그룹 멤버 전체 조회, cursor/page 페이지네이션 모두 대응 |
| `eto_newsletter/service.py` | `GroupComparison` dataclass, `compare_with_main_group()` 추가; `send_to_selected()` 성공 후 `MAIN_GROUP` 재생성 |
| `eto_newsletter/app.py` | `ComparisonDialog`, Compare 버튼, Before-sending 체크리스트/게이트, 5초 카운트다운(QTimer), CSV 재업로드 시 상태 초기화 |
| `README.md` | 기능 설명 및 월간 사용 절차 갱신 |

## 검증

- `py_compile` 4개 모듈 통과, `import eto_newsletter.app` 정상.
- Fake client 유닛 테스트: 신규/탈퇴/수신거부 분류(대소문자 무시 포함),
  그룹 없음 케이스, 페이지네이션 종료 조건 모두 통과.

## 설계 메모

- `Korea contacts` 그룹을 발송 때마다 삭제 후 재생성하므로 MailerLite 대시보드
  상의 그룹 ID는 매달 바뀜. 그룹 삭제는 구독자 자체를 지우지 않음(수신거부
  상태는 구독자에 저장되므로 보존됨).
- 탈퇴(Departed)와 수신거부(Unsubscribed)는 별개 개념이라 분리 표기: 전자는
  CSV 명단 관리상 빠진 사람, 후자는 본인이 메일의 unsubscribe 링크를 누른 사람.
