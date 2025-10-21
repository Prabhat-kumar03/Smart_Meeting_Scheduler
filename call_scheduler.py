from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from typing import Annotated
from typing_extensions import TypedDict
from datetime import datetime
from zoneinfo import ZoneInfo
from pytz import timezone
import os.path
import dotenv, os, getpass
import uuid

from pydantic import BaseModel

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

dotenv.load_dotenv(".env")
KEY = os.environ.get("GOOGLE_API_KEY")
if not KEY:
    os.environ["GOOGLE_API_KEY"] = getpass.getpass("Your API Key here :")

class SlotSchema(BaseModel):
    date: str
    start_time: str
    end_time: str

class State(TypedDict):
    user_query: str
    date: str
    start_time: str
    end_time: str
    event: any
    service: any
    occupied_slots_list: list
    slot_available: bool
    event_created: bool
    meet_link: str
    messages: Annotated[list, add_messages]

graph_compiler = StateGraph(State)

def set_system_prompt(state: State) -> State:
    """Node to set a default system prompt."""
    try:

        now_utc = datetime.now(timezone("UTC"))
        now_ist = now_utc.astimezone(timezone("Asia/Kolkata"))
        now = now_ist.isoformat()
 
        prompt_content = f"""You are an Advanced AI agent capable of understaning the user intent from chat history. Your name is Luna.\
        Based on the data passed you need to extract the date and time user wants to schedule a meeting.
        
        ##CASE 1 - You are able to get date, start time and end time using chat history to schedule a call then convert date, start_time and end_time to uct format, just like : {now}.  
        
        """

        system_prompt= [SystemMessage(content=prompt_content)] 
        return {"messages": system_prompt}
    except Exception as e:
        print("Exception occured while setting prompt -", e)

def create_calender_services(state: State) -> State:
    """Node to instantiate Google Calender Service"""

    try:
        SCOPES = ["https://www.googleapis.com/auth/calendar"]
        creds = None
         
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
         
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                 
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=8080)
                 
            # Save the credentials for the next run
            with open("token.json", "w") as token:
                token.write(creds.to_json())
                 
         
        service = build(
            "calendar", "v3", credentials=creds
        )  # create a calender service
        state["service"] = service
        return state


    except Exception as error:
        print("Error occured while create calender service - > ", error)

def take_user_input(state: State) -> State:
    """Node to take input from user"""
    try:
        user_input = input("Please enter the details of date, start time and end time of the booking __ ")
        return {"user_query": user_input} 
    except Exception as e:
        print("Error occured while taking user input - > ", e)

def extract_date_time(state: State) -> State:
    try:

        llm = init_chat_model(
            model="gemini-2.5-flash", model_provider="google_genai"
        )
        llm = llm.with_structured_output(schema=SlotSchema)
        system_msg = None
        if "messages" in state and len(state["messages"]) > 0:
            system_msg = state["messages"][0]
        else:
            system_msg = SystemMessage(content="You are Luna, an AI assistant that extracts meeting details.")
        message = [
            state["messages"][0], (HumanMessage(content=state["user_query"]))
        ] 
        
        message = [system_msg, HumanMessage(content=state.get("user_query", ""))]
        response = llm.invoke(message)
        print("Model_output :",response)

        if response.date and response.start_time and response.end_time:
            return {
                "date": response.date,
                "start_time": response.start_time,
                "end_time": response.end_time,
            }

        return {"date" : None,
                "start_time":None,
                "end_time":None,
                "messages":[AIMessage(content="Couldn't extract date/time. Please Clarify")]}
    except Exception as e:
        print("error occured while exctrating date and time.", e)
        return{
            "date" : None,
            "start_time":None,
            "end_time":None
        }

def check_slot(state: State) -> State:
    """Node to check and book slots if avaialble"""
    try:
        start_time = state["start_time"]
        end_time = state["end_time"]

        # If they are datetime objects, convert to ISO strings
        if isinstance(start_time, datetime):
            start_time = start_time.isoformat()
        if isinstance(end_time, datetime):
            end_time = end_time.isoformat()
        body = {
            "timeMin": state["start_time"],
            "timeMax": state["end_time"],
            "items": [{"id": "primary"}],
        }
        events_result = state["service"].freebusy().query(body=body).execute()
        busy_times = events_result["calendars"]["primary"]["busy"]
        if len(busy_times) == 0:  # True if free
            event = {
                "summary": "Team Meeting",
                "location": "Conference Room",
                "description": "Discuss project updates.",
                "start": {
                    "dateTime": state["start_time"],
                    "timeZone": "Asia/Kolkata",
                },
                "end": {
                    "dateTime": state["end_time"],
                    "timeZone": "Asia/Kolkata",
                },
                "attendees": [
                    {"email": "prabhat21137@recmainpuri.in"},
                    {"email": "kmarprabhat164@gmail.com"},
                ],
                "reminders": {
                    "useDefault": True,
                },
            }
            print(" --- slot checking completed successfully -----")
            return {"event": event, "slot_available": True}

        return {"slot_available": False, "occupied_slots_list": busy_times}
    except Exception as e:
        print("Error occured while checking slots availibilty - > ", e)
        return {"slot_available": False, "error": str(e)}

def inform_occupied_slots(state: State) -> State:
    """Node to check if slot is free or not,
    if so then book it, else return the list
    of booked slots."""
    try: 
        # print the occupied array of slots - 
        user_input = input(
            "Hey there, selected slot is currently occupied, please select some different slot."
        )
 
        return {"user_query": user_input}
    except Exception as e:
        print(
            "Error occured while taking user input and informing about full slots - > ",
            e,
        )

def create_event(state: State) -> State:
    """Node to book the slot"""
    try: 
        event = state["event"]
        event["conferenceData"] = {
            "createRequest": {
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
                "requestId": str(uuid.uuid4())  # unique ID required
            }
        }
        created_event = (
            state["service"].events().insert(calendarId="primary", body=state["event"],conferenceDataVersion=1).execute()
        )
        print("Luna: Event created: %s" % (created_event.get("htmlLink")))

        return {"event_created": True, "meet_link": created_event.get("hangoutLink")}
    except Exception as e:
        print("Error while booking the specified event: ", e)

def routing_date_time(state: State):
    """Roouting Node to check if we have date and time"""
    if state.get("date") and state.get("end_time") and state.get("start_time"):
        return "yes"
    return "no"

def routing_check_slot(state: State) -> State:
    if state["slot_available"]:
        return "yes"
    return "no"

graph_compiler.add_node("set_system_prompt", set_system_prompt)
graph_compiler.add_node("create_calender_service", create_calender_services)
graph_compiler.add_node("take_user_input", take_user_input)
graph_compiler.add_node("extract_date_time", extract_date_time)
# graph_compiler.add_node("routing_date_time", routing_date_time)
graph_compiler.add_node("check_slot", check_slot)
graph_compiler.add_node("inform_occupied_slots", inform_occupied_slots)
graph_compiler.add_node("create_event", create_event)


graph_compiler.add_edge(START, "set_system_prompt")
graph_compiler.add_edge("set_system_prompt", "create_calender_service")
graph_compiler.add_edge("create_calender_service", "take_user_input")
graph_compiler.add_edge("take_user_input", "extract_date_time")  

graph_compiler.add_conditional_edges(
    "extract_date_time",
    routing_date_time,
    {"yes": "check_slot", "no": "take_user_input"},
)

# graph_compiler.add_edge("routing_date_time", "extract_date_time")

graph_compiler.add_conditional_edges(
    "check_slot",
    routing_check_slot,
    {"yes": "create_event", "no": "inform_occupied_slots"},
)

graph_compiler.add_edge("inform_occupied_slots", "extract_date_time")

graph_compiler.add_edge("create_event", END)
graph_compiler
app = graph_compiler.compile()

for result in app.stream({}):
    print(result)
try:
    png_bytes = app.get_graph().draw_mermaid_png()

    with open("my_graph.png", "wb") as f:
        f.write(png_bytes)
except Exception:
    # This requires some extra dependencies and is optional
    pass