#!/usr/bin/python
# -*- coding: utf-8 -*-
import ast
import flask
import json
import re
import requests

app = flask.Flask(__name__)

def increaseCount(response):
    #Increase the user API requests counter if the request was to a user API
    if "/api/v1/users" in flask._request_ctx_stack.top.request.url and response.status_code in (200, 201, 204, 400, 405):
            #Counter is increased by adding a row to a mongodb collection
            d = dict()
            d["flag"] = "w"
            d["table"] = "counter_users"
            d["column"] = ["dummy"]
            d["data"] = [""]
            requests.post(url=users_write_url, json=d)
    return response

app.after_request(increaseCount)
orchestrator_ip = '3.211.227.189'
users_write_url = "http://"+orchestrator_ip+"/api/v1/db/write"
users_read_url = "http://"+orchestrator_ip+"/api/v1/db/read"


# API 1

@app.route("/api/v1/users", methods=["PUT", "GET"])
def user():
    #Create a user if PUT method is used with new username and valid SHA-1 hashed password 
    #Return response in the form of a JSON array of registered users if GET method is used
    if flask.request.method == "PUT":
        try:
            username = flask.request.get_json()["username"]
            password = flask.request.get_json()["password"].lower()
        except:
            return flask.Response("Invalid request format!", status=400)
        d = dict()
        d["table"] = "users"
        d["column"] = ["username"]
        d["data"] = [username]
        if len(ast.literal_eval(requests.post(url=users_read_url, json=d).text)) != 0:
            return flask.Response("Username already exists!", status=400)
        if not re.search("^[a-fA-F0-9]{40}$", password):
            return flask.Response("Invalid Password: not in SHA-1 format", status=400)
        d["column"] = ["username", "password"]
        d["data"] = [username, password]
        d["flag"] = "w"
        requests.post(url=users_write_url, json=d)
        return flask.Response("{}", status=201, mimetype="application/json")
        return flask.Response("Username already exists!", status=400)
    else:
        d = dict()
        d['table'] = "users"
        d["column"] = ["username"]
        d["data"] = [""]
        l = requests.post(url=users_read_url, json=d)
        if len(ast.literal_eval(l.text)) == 0:
            #If no users are registered, return 204
            return flask.Response(status=204)
        return flask.Response(json.dumps([_["username"] for _ in ast.literal_eval(l.text)]), status=200, mimetype="application/json")


# API 2

@app.route("/api/v1/users/<name>", methods=["DELETE"])
def removeUser(name):
    #Remove user if they exist
    d = dict()
    d["table"] = "users"
    d["column"] = ["username"]
    d["data"] = [name]
    if len(ast.literal_eval(requests.post(url=users_read_url, json=d).text)) == 0:
        return flask.Response("User doesn't exist!", status=400)
    d["flag"] = "d"
    requests.post(url=users_write_url, json=d)
    #Remove rides created by user and also remove user from rides they have joined
    requests.delete(url="http://RideShareLB-465441621.us-east-1.elb.amazonaws.com/api/v1/db/clean_rides/"+name)
    return flask.Response("{}", status=200, mimetype="application/json")


# API 11

@app.route("/api/v1/_count", methods=["GET", "DELETE"])
def counter():
    #if GET method is used, return count of requests received by user APIs as a JSON array
    #if DELETE method is used, reset count of requests received by user APIs to 0
    d = dict()
    d["table"] = "counter_users"
    d["column"] = ["dummy"]
    d["data"] = [""]
    if flask.request.method == "GET":
        return flask.Response(json.dumps([len(ast.literal_eval(requests.post(url=users_read_url, json=d).text))]), status=200, mimetype="application/json")
    else:
        d["flag"] = "d"
        requests.post(url=users_write_url, json=d)
        return flask.Response("{}", status=200, mimetype="application/json")


# API Healthcheck

@app.route("/api/v1/healthcheck", methods=["GET"])
def healthCheck():
    #Return 200 status code for AWS healthcheck purposes
    return flask.Response("{}", status=200)
