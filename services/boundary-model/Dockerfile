FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.14.0 torchvision==0.29.0 \
    && python -m pip install --no-cache-dir ultralytics==8.3.217 opencv-python==5.0.0.93 \
    && python -c 'import cv2, torch, torchvision; from ultralytics import YOLO'

RUN useradd --create-home --uid 10001 fieldshift && install -d -o fieldshift -g fieldshift /models
WORKDIR /app
COPY serve.py .
USER fieldshift
ENV PYTHONUNBUFFERED=1 YOLO_AUTOINSTALL=false
CMD ["python", "serve.py", "--host", "0.0.0.0", "--weights", "/models/DelineateAnythingv2.pt", "--download-weights"]
