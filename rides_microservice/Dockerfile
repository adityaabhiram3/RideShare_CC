FROM python:alpine 

RUN pip install gunicorn flask requests

WORKDIR /src

EXPOSE 5000

CMD ["gunicorn", "--workers=3", "--threads=6", "-b", "0.0.0.0:5000", "wsgi:app"]
