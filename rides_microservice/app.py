#!/usr/bin/python
# -*- coding: utf-8 -*-
import ast
import csv
import datetime
import flask
import json
import random
import requests

app = flask.Flask(__name__)

def increaseCount(response):
    #Increase the ride API requests counter if the request was to a ride API
    if "/api/v1/rides" in flask._request_ctx_stack.top.request.url and response.status_code in (200, 201, 204, 400, 405):
            #Counter is increased by adding a row to a mongodb collection
            d = dict()
            d["flag"] = "w"
            d["table"] = "counter_rides"
            d["column"] = ["dummy"]
            d["data"] = [""]
            requests.post(url=rides_write_url, json=d)
    return response

app.after_request(increaseCount)

#Parse CSV file to get valid locations
locations = list()
with open("AreaNameEnum.csv") as csvfile:
    reader = csv.reader(csvfile, delimiter=",")
    next(reader)
    for row in reader:
        locations.append(int(row[0]))

rides_ip = requests.get("https://checkip.amazonaws.com").text[:-1]
orchestrator_ip = '3.211.227.189'
rides_write_url = "http://"+orchestrator_ip+"/api/v1/db/write"
rides_read_url = "http://"+orchestrator_ip+"/api/v1/db/read"


# API 3

@app.route("/api/v1/rides", methods=["POST"])
def createRide():
    #Create a ride, given valid source, destination, timestamp and user in the body
    try:
        body = flask.request.get_json()
        timestamp, created_by, source, destination = body["timestamp"], body["created_by"], int(body["source"]), int(body["destination"])
    except:
        return flask.Response("Invalid request format!", status=400)
    try:
        datetime.datetime.strptime(timestamp, "%d-%m-%Y:%S-%M-%H")
    except:
        return flask.Response("Invalid timestamp!", status=400)
    if source not in locations or destination not in locations or source == destination:
        return flask.Response("Invalid source or destination!", status=400)
    #Check if user exists by sending GET request to the users API
    users_list_response = requests.get(url="http://RideShareLB-465441621.us-east-1.elb.amazonaws.com/api/v1/users", headers={"Origin": rides_ip})
    if users_list_response.status_code == 204 or created_by not in ast.literal_eval(users_list_response.text):
        return flask.Response("User doesn't exist!", status=400)
    d = dict()
    d["table"] = "rides"
    d["column"] = ["ride_id"]
    while True:
        #Generate a random ID for he ride between 1 and 100000 (both inclusive)
        new_ride_id = random.randint(1,100000)
        d["data"] = [new_ride_id]
        #Check if the generated ID is new
        if len(ast.literal_eval(requests.post(url=rides_read_url, json=d).text)) == 0:
            break
    d["column"] = ["ride_id", "timestamp", "created_by", "source", "destination"]
    d["data"] = [new_ride_id, timestamp, created_by, source, destination]
    d["flag"] = "w"
    requests.post(url=rides_write_url, json=d)
    d["table"] = "users_rides"
    d["column"] = ["username", "ride_id"]
    d["data"] = [created_by, new_ride_id]
    #Store user and ride in auxiliary collection
    d = requests.post(url=rides_write_url, json=d)
    return flask.Response("{}", status=201, mimetype="application/json")


# API 4

@app.route("/api/v1/rides", methods=["GET"])
def upcomingRides():
    #Return JSON array of upcoming rides for given source and destination
    try:
        source = int(flask.request.args.get("source", None))
        destination = int(flask.request.args.get("destination", None))
    except:
        return flask.Response("Invalid request format!", status=400)
    if source not in locations or destination not in locations or source == destination:
        return flask.Response("Invalid source or destination!", status=400)
    d = dict()
    d["column"] = ["source", "destination"]
    d["data"] = [source, destination]
    d["table"] = "rides"
    l = requests.post(url=rides_read_url, json=d)
    if len(ast.literal_eval(l.text)) == 0:
        #If there are no rides with given source and destination, return 204
        return flask.Response(status=204)
    l = ast.literal_eval(l.text)
    #Filter out only the upcoming rides
    filtered_list = list()
    for row in l:
        ride_time = datetime.datetime.strptime(row["timestamp"], "%d-%m-%Y:%S-%M-%H")
        current_time = datetime.datetime.now()
        #Compare ride timestamp and current time
        if ride_time > current_time:
            filtered_list.append({"rideId": row["ride_id"],
                                  "username": row["created_by"],
                                  "timestamp": row["timestamp"]})
    if len(filtered_list) == 0:
        #If there are no upcoming rides with given source and destination, return 204
        return flask.Response(status=204)
    return flask.Response(json.dumps(filtered_list), status=200, mimetype="application/json")


# API 5

@app.route("/api/v1/rides/<rideId>", methods=["GET"])
def getRideDetails(rideId):
    #Return the JSONified details of ride corresponding to given rideID 
    d = dict()
    d["column"] = ["ride_id"]
    try:
        ride_id = int(rideId)
    except ValueError:
        return flask.Response("Invalid ride ID!", status=400)
    d["data"] = [ride_id]
    d["table"] = "rides"
    details = ast.literal_eval(requests.post(url=rides_read_url, json=d).text)
    if len(details) == 0:
        #If there is no ride associated with the rideID, return 204
        return flask.Response(status=204)
    d["column"] = ["ride_id"]
    d["data"] = [ride_id]
    d["table"] = "users_rides"
    users = [_["username"] for _ in ast.literal_eval(requests.post(url=rides_read_url, json=d).text)]
    users.remove(details[0]["created_by"])
    response = str({
        "rideId": ride_id,
        "created_by": details[0]["created_by"],
        "users": users,
        "timestamp": details[0]["timestamp"],
        "source": details[0]["source"],
        "destination": details[0]["destination"],
        })
    return flask.Response(json.dumps(response), status=200, mimetype="application/json")


# API 6

@app.route("/api/v1/rides/<rideId>", methods=["POST"])
def joinRide(rideId):
    #Join a user to a ride, given the user and ride exist and that the ride has not started yet
    d = dict()
    d["column"] = ["ride_id"]
    try:
        ride_id = int(rideId)
    except ValueError:
        return flask.Response("Invalid ride ID!", status=400)
    d["data"] = [ride_id]
    d["table"] = "rides"
    ride_details = ast.literal_eval(requests.post(url=rides_read_url, json=d).text)
    if len(ride_details) == 0:
        return flask.Response("Ride doesn't exist", status=400)
    timestamp = ride_details[0]["timestamp"]
    ride_time = datetime.datetime.strptime(timestamp, "%d-%m-%Y:%S-%M-%H")
    current_time = datetime.datetime.now()
    #Compare timestamp of ride and current time
    if ride_time < current_time:
        return flask.Response("The ride has already started!", status=400)
    try:
        username = flask.request.get_json()["username"]
    except:
        return flask.Response("Invalid request format!", status=400)
    if ride_details[0]["created_by"] == username:
        return flask.Response("User cannot join their own ride!", status=400)
    #Check if user exists by sending GET request to the users API
    users_list_response = requests.get(url="http://RideShareLB-465441621.us-east-1.elb.amazonaws.com/api/v1/users", headers={"Origin": rides_ip})
    if users_list_response.status_code == 204 or username not in ast.literal_eval(users_list_response.text):
        return flask.Response("User doesn't exist!", status=400)
    d["column"] = ["username", "ride_id"]
    d["data"] = [username, ride_id]
    d["table"] = "users_rides"
    if ast.literal_eval(requests.post(url=rides_read_url, json=d).text):
        return flask.Response("User has already joined the ride!", status=400)
    d["flag"] = "w"
    #Store in auxiliary collection to indicate that the user is part of the ride
    requests.post(url=rides_write_url, json=d)
    return flask.Response("{}", status=200, mimetype="application/json")


# API 7

@app.route("/api/v1/rides/<rideId>", methods=["DELETE"])
def deleteRide(rideId):
    #Delete a ride if it exists
    d = dict()
    d["table"] = "rides"
    d["column"] = ["ride_id"]
    try:
        ride_id = int(rideId)
    except ValueError:
        return flask.Response("Invalid ride ID!", status=400)
    d["data"] = [ride_id]
    if len(ast.literal_eval(requests.post(url=rides_read_url, json=d).text)) == 0:
        return flask.Response("Ride doesn't exist!", status=400)
    d["flag"] = "d"
    requests.post(url=rides_write_url, json=d)
    d["table"] = "users_rides"
    #Remove the ride from auxiliary collection
    requests.post(url=rides_write_url, json=d)
    return flask.Response("{}", status=200, mimetype="application/json")

# API 11

@app.route("/api/v1/_count", methods=["GET", "DELETE"])
def counter():
    #if GET method is used, return count of requests received by user APIs as a JSON array
    #if DELETE method is used, reset count of requests received by user APIs to 0
    d = dict()
    d["table"] = "counter_rides"
    d["column"] = ["dummy"]
    d["data"] = [""]
    if flask.request.method == "GET":
        return flask.Response(json.dumps([len(ast.literal_eval(requests.post(url=rides_read_url, json=d).text))]), status=200, mimetype="application/json")
    else:
        d["flag"] = "d"
        requests.post(url=rides_write_url, json=d)
        return flask.Response("{}", status=200, mimetype="application/json")


# API 12

@app.route("/api/v1/rides/count", methods=["GET"])
def countRides():
    #Return number of rides created as a JSON array
    d = dict()
    d["table"] = "rides"
    d["column"] = ["ride_id"]
    d["data"] = [""]
    return flask.Response(json.dumps([len(ast.literal_eval(requests.post(url=rides_read_url, json=d).text))]), status=200, mimetype="application/json")


# API 0

@app.route("/api/v1/db/clean_rides/<username>", methods=["DELETE"])
def cleanDeletedUser(username):
    #Remove records of given user from rides database
    #This API is used by remove user API
    d = dict()
    d["table"] = "rides"
    d["column"] = ["created_by"]
    d["data"] = [username]
    d["flag"] = "d"
    requests.post(url=rides_write_url, json=d)
    d["table"] = "users_rides"
    d["column"] = ["username"]
    requests.post(url=rides_write_url, json=d)
    return flask.Response("{}", status=200, mimetype="application/json")


# API Healthcheck

@app.route("/api/v1/healthcheck", methods=["GET"])
def healthCheck():
    #Return 200 status code for AWS healthcheck purposes
    return flask.Response("{}", status=200)
