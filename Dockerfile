FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . /app/

# Make sure execution scripts are executable
RUN chmod +x run-copy-trade.sh

# Run the copy trade script
CMD ["python", "copy_trade_runner.py"]
