FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYVISTA_OFF_SCREEN=true VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow
RUN apt-get update && apt-get install -y --no-install-recommends libegl1 libgl1 libglib2.0-0 libxrender1 libsm6 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
ENTRYPOINT ["python", "cad_benchmark.py"]
CMD ["--help"]
