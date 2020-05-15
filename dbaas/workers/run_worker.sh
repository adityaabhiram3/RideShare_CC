#!/bin/bash

until mongo --eval "print(\"waited for connection\")"
  do
    sleep 3
  done
/usr/bin/python3 /src/worker.py
