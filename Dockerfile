FROM python:3.10.5-alpine3.16

RUN apk add make jpeg-dev zlib-dev alpine-sdk \
    && python -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt makefile ./
RUN pip install --no-cache-dir "python-telegram-bot[job-queue,webhooks]>=20" && \
    make install

WORKDIR /app
COPY . /app

ENTRYPOINT [ "make" ] 
CMD [ "run" ]
