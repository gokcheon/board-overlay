"""앱 버전 — 단일 진실 (fix28).

매 배포마다 여기 숫자만 +1 하면:
  · 런처 화면 표시 버전
  · 자동 업데이트의 "내 버전" 비교값
  · 설치마법사 AppVersion (빌드.bat이 여기서 읽어 주입)
이 전부 함께 바뀐다. 다른 곳에 버전을 하드코딩하지 말 것.
"""
APP_FIX = 42                      # fix 번호 (자동 업데이트 비교 기준)
APP_VERSION = f"1.0.{APP_FIX}"    # 설치마법사 AppVersion 표기
APP_LABEL = f"fix{APP_FIX}"       # 화면 표시용

if __name__ == "__main__":
    # `python -m server.version`       → 1.0.33  (빌드.bat·Actions가 설치마법사 AppVersion으로)
    # `python -m server.version label` → fix33   (올리기.bat이 릴리스 태그로)
    import sys
    print(APP_LABEL if "label" in sys.argv else APP_VERSION)
