# Qwen2-VL FinanceEvaluationPage 연결

첨부 노트북은 어댑터를 Google Drive의
`/content/drive/MyDrive/qwen2vl_receipt_output/best_adapter`에 저장합니다.
웹앱은 이 어댑터를 직접 포함하지 않고 HTTPS 추론 엔드포인트로 호출합니다.

1. Colab에서 Google Drive를 마운트합니다.
2. `backend/scripts/qwen_vl_colab_server.py`를 Colab에 업로드합니다.
3. 필요한 패키지를 설치하고 서버를 실행합니다.

   ```python
   !pip install -q fastapi uvicorn unsloth
   %env QWEN_VL_API_TOKEN=충분히-긴-임의의-비밀값
   !python qwen_vl_colab_server.py
   ```

4. Cloudflare Tunnel, ngrok 등으로 Colab의 8002 포트를 HTTPS로 노출합니다.
5. 웹앱 `.env`에 값을 넣고 backend를 다시 시작합니다.

   ```dotenv
   QWEN_VL_API_URL=https://YOUR-TUNNEL.example/predict
   QWEN_VL_API_TOKEN=Colab에-설정한-동일한-값
   QWEN_VL_MODEL_NAME=qwen2-vl-receipt-finetuned
   ```

연결되면 `FinanceEvaluationPage`의 평가 모델 목록에
`qwen2-vl-receipt-finetuned`가 나타납니다. 평가 실행 시 OCR 텍스트가 아니라
업로드한 원본 영수증 이미지가 Qwen-VL에 전달되고, 기존 Ollama 모델과 동일한
필드 정확도 및 Excel 생성 검증 결과로 표시됩니다.

Colab 런타임이 종료되거나 터널 URL이 바뀌면 모델은 목록에서 보이더라도 호출이
실패합니다. 고정 운영 환경에서는 동일한 `/predict` 계약으로 GPU 서버에 배포하세요.
