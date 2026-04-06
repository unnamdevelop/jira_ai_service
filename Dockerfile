FROM python:3.11-slim
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fix OpenAI httpx compatibility issue
RUN pip install httpx==0.27.0

# Copy application code
COPY . .

# Create reports directory
RUN mkdir -p reports

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
