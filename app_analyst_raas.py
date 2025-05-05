# Public Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/rest-api
# This demo is not supported in SiS. You must run this streamlit locally.

import json
import re
from typing import Any, Generator, Iterator

import pandas
import pandas as pd
import requests
import snowflake.connector
import sseclient
import streamlit as st
import spcs_helpers
from io import StringIO  
import time
from typing import Dict, List, Optional, Tuple, Union, Any
import pandas as pd
from streamlit.web.server.websocket_headers import _get_websocket_headers
import logging
import sys
import warnings



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)
logger.info("🔧 Logging system is now initialized.")
warnings.filterwarnings('ignore', category=UserWarning)



# user = _get_websocket_headers().get("Sf-Context-Current-User") or "Visitor"
# st.write(st.context.headers.get_all("pragma"))
user=st.context.headers['Sf-Context-Current-User']
# user='ASH_K'
# st.write("Sf-Context-Current-User",user)

STAGE = "RAW_DATA"
FILE = "sales_semantic_model.yml"

@st.cache_resource
def connect_to_snowflake():
    return spcs_helpers.session()
st.session_state.RAW_CONN = spcs_helpers.connection()
if "RAW_CONN" not in st.session_state or st.session_state.RAW_CONN is None:
    st.session_state.RAW_CONN = connect_to_snowflake()


def get_conversation_history() -> list[dict[str, Any]]:
    # st.write("Current messages state:", st.session_state.messages)
    messages = []
    for msg in st.session_state.messages:
        role = msg.get("role", "user")
        message_content = []

        content_blocks = msg.get("content", [])

        for block in content_blocks:
            if isinstance(block, str):
                clean_text = block.strip()
                if clean_text:
                    message_content.append({"type": "text", "text": clean_text})

            elif isinstance(block, dict):
                # Only process 'text' type and skip 'sql' and 'dataframe'
                block_type = block.get("type", "text")
                if block_type in ["sql", "dataframe", "request_id"]:
                    continue

                # Safely get text or value
                if "text" in block:
                    value = block["text"]
                elif "value" in block:
                    value = block["value"]
                else:
                    value = ""

                try:
                    clean_text = str(value).strip()
                except Exception:
                    clean_text = ""

                if clean_text:
                    message_content.append({"type": block_type, "text": clean_text})

        if message_content:
            messages.append({"role": role, "content": message_content})
    # st.write("messages prepared by get_conversation_history", messages)
    return messages





def send_message() -> requests.Response:
    """Calls the REST API and returns a streaming client."""
    request_body = {
        "messages": get_conversation_history(),
        "semantic_model_file": f"@{st.session_state.RAW_CONN.database}.{st.session_state.RAW_CONN.schema}.{STAGE}/{FILE}",
        "stream": True,
    }
    # st.write("Sending request body:", json.
    # dumps(request_body, indent=2))
    resp = requests.post(
        url=f"https://{st.session_state.RAW_CONN.host}/api/v2/cortex/analyst/message",
        json=request_body,
        headers={
            "Authorization": f'Snowflake Token="{st.session_state.RAW_CONN.rest.token}"',
            "Content-Type": "application/json",
        },
        stream=True,
    )
    if resp.status_code < 400:
        return resp  # type: ignore
    else:
        raise Exception(f"Failed request with status {resp.status_code}: {resp.text}")


def stream(events: Iterator[sseclient.Event]) -> Generator[Any, Any, Any]:
    prev_index = -1
    prev_type = ""
    prev_suggestion_index = -1
    while True:
        event = next(events, None)
        if not event:
            return
        data = json.loads(event.data)
        new_content_block = event.event != "message.content.delta" or data["index"] != prev_index

        if prev_type == "sql" and new_content_block:
            # Close sql markdown once sql section finishes.
            yield "\n```\n\n"
        match event.event:
            case "message.content.delta":
                match data["type"]:
                    case "sql":
                        if new_content_block:
                            # Add sql markdown when we enter a new sql block.
                            yield "```sql\n"
                        yield data["statement_delta"]
                    case "text":
                        yield data["text_delta"]
                    case "suggestions":
                        if new_content_block:
                            # Add a suggestions header when we enter a new suggestions block.
                            yield "\nHere are some example questions you could ask:\n\n"
                            yield "\n- "
                        elif (
                            prev_suggestion_index != data["suggestions_delta"]["index"]
                        ):
                            yield "\n- "
                        yield data["suggestions_delta"]["suggestion_delta"]
                        prev_suggestion_index = data["suggestions_delta"]["index"]
                prev_index = data["index"]
                prev_type = data["type"]
            case "status":
                st.session_state.status = data["status_message"]
                # We return here to allow the spinner to update with the latest status, but this method will be
                #  called again for the next iteration
                return
            case "error":
                st.session_state.error = data
                return



def display_df(df: pd.DataFrame) -> None:
    if len(df.index) >= 1:
        data_tab, line_tab, bar_tab = st.tabs(["Data", "Line Chart", "Bar Chart"])
        data_tab.dataframe(df)

        index_col = df.columns[0]
        default_cols = df.columns.tolist()

        def render_chart(tab, chart_type: str, selected_cols: list):
            """Render chart based on selected columns."""
            if selected_cols:
                try:
                    chart_df = df[[index_col] + selected_cols].set_index(index_col)
                    if chart_type == "line":
                        tab.line_chart(chart_df)
                    else:
                        tab.bar_chart(chart_df)
                except Exception as e:
                    tab.warning(f"Could not render {chart_type} chart: {e}")
            else:
                tab.info(f"Please select at least one numeric column for the {chart_type} chart.")

        # Handle charts based on the number of columns
        if len(df.columns) == 2:  # Exactly 2 columns (year and total_revenue)
            chart_df = df.set_index(index_col)
            with line_tab:
                st.line_chart(chart_df)
            with bar_tab:
                st.bar_chart(chart_df)
        # elif len(df.columns) <= 3:
        #     chart_df = df.set_index(index_col)
        #     with line_tab:
        #         st.line_chart(chart_df)
        #     with bar_tab:
        #         st.bar_chart(chart_df)
        # else:
        #     # User selects columns for line chart
        #     with line_tab:
        #         st.markdown("**Select columns to plot (Line Chart):**")
        #         selected_line_cols = st.multiselect(
        #             "Line Chart Columns", default_cols, default=default_cols, key="line_cols"
        #         )
        #         render_chart(line_tab, "line", selected_line_cols)

        #     # User selects columns for bar chart
        #     with bar_tab:
        #         st.markdown("**Select columns to plot (Bar Chart):**")
        #         selected_bar_cols = st.multiselect(
        #             "Bar Chart Columns", default_cols, default=default_cols, key="bar_cols"
        #         )
        #         render_chart(bar_tab, "bar", selected_bar_cols)
    else:
        st.dataframe(df)



        

def process_message(prompt: str) -> None:
    st.session_state.messages.append({"role": "user", "content": [prompt]})
    with st.chat_message("user"):
        st.markdown(prompt)

    accumulated_text = []
    with st.chat_message("assistant"):
        with st.spinner("Sending request..."):
            request_start_time = time.time()
            response = send_message()
            request_duration = time.time() - request_start_time

        with st.expander("Request Info"):
            st.markdown(
                f"""
                - `Request ID`: `{response.headers.get('X-Snowflake-Request-Id')}`  
                - `API Response Time`: `{request_duration:.2f} seconds`
                """
            )
            
        accumulated_text.append({
            "type": "request_id",
            "text": response.headers.get('X-Snowflake-Request-Id'),
            "execution_time": request_duration
        })            

        # Get the SSE stream from response
        events = sseclient.SSEClient(response).events()        
        streamed_text=""
        stream_start_time = time.time()        
        while st.session_state.status.lower() != "done":
            with st.spinner(st.session_state.status):
                for chunk in stream(events):
                    streamed_text+=chunk
        stream_response_duration = time.time() - stream_start_time                    
        # Clean up text (remove awkward line breaks)
        cleaned_text = re.sub(r"\n\s*", " ", streamed_text).strip()

        # Extract SQL blocks
        sql_pattern = r"```sql\s*(.*?)\s*```"
        sql_blocks = re.findall(sql_pattern, cleaned_text, re.DOTALL | re.IGNORECASE)                
        
        # Remove SQL blocks from the text
        text_only = re.sub(
            r"(This is our interpretation of your question:\s*)",
            r"***\1***\n\n",  # bold + italic with newlines
            re.sub(sql_pattern, "", cleaned_text, flags=re.DOTALL),
            flags=re.IGNORECASE
        ).strip()

        # Show text output
        if text_only:
            lines = text_only.strip().split(" - ")
            formatted = "\n".join([f"- {line.strip()}" for line in lines if line.strip()])                        
            st.markdown(formatted)
            accumulated_text.append({
                "type": "text",
                "text": formatted,
                "execution_time": stream_response_duration
            })

        # Show SQL only inside expander        
        if sql_blocks:                                                 
            for sql_query in sql_blocks:                
                with st.spinner("Executing Query"):
                    sql_exec_start = time.time()
                    df = pd.read_sql(sql_query, st.session_state.RAW_CONN)
                    sql_exec_duration = time.time() - sql_exec_start
                    accumulated_text.append({
                        "type": "sql",
                        "text": sql_blocks[0],
                        "execution_time": sql_exec_duration
                    })
                    accumulated_text.append({
                        "type": "dataframe",
                        "text": df,
                        "execution_time": "None"
                    })   
                    with st.expander("sql query"):
                        st.code(sql_blocks[0], language="sql")    
                        st.markdown(f"🕒 - `SQL Execution Time`: `{sql_exec_duration:.2f} seconds`")                    
                        st.markdown(f"""🕒 - `Stream Time`: `{stream_response_duration:.2f} seconds`""")                                                                               
                    display_df(df)
    display_feedback(response.headers.get('X-Snowflake-Request-Id'))      
    st.session_state.status = "Interpreting question"    
    st.session_state.messages.append(
        {"role": "analyst", "content": accumulated_text}
    )


    


def show_conversation_history() -> None:
    len_msgs = len(st.session_state.messages) - 1    
    for idx, message in enumerate(st.session_state.messages):        
        chat_role = "assistant" if message["role"] == "analyst" else "user"
        with st.chat_message(chat_role):
            for content in message["content"]:
                if isinstance(content, dict) and content.get("type") == "request_id":
                    with st.expander("Request ID"):
                        st.code(content["text"])
                    st.caption(f"🕓 API Response Time: {content.get('execution_time', 0):.2f} sec")
                elif isinstance(content, dict) and content.get("type") == "text":
                    st.write(content["text"])
                    st.caption(f"🕓 Stream Time: {content.get('execution_time', 0):.2f} sec")
                elif isinstance(content, dict) and content.get("type") == "sql":
                    with st.expander("sql query"):
                        st.code(content["text"], language="sql")
                    st.caption(f"🕓 SQL Execution Time: {content.get('execution_time', 0):.2f} sec")
                elif isinstance(content, dict) and content.get("type") == "dataframe":
                    display_df(content["text"])
                elif isinstance(content, Exception):
                    st.error(f"Error while processing request:\n {content}", icon="🚨")
                else:
                    st.write(content)
        if idx == len_msgs:
            if "feedback" in message:
                st.write(f"Feedback: {message['feedback']}")
            elif 'request_id' in message:
                request_id = message['request_id']
                display_feedback(request_id)
            elif chat_role == 'analyst':
                if "feedback" in message:
                    st.write(f"You rated Analyst response as: {message['feedback']}")
                else:
                    st.write("Feedback: N/A")                                           
                         

def submit_feedback_with_message(request_id: str, positive: bool, feedback_key: str):
    feedback_msg = st.session_state.get(feedback_key, "")
    submit_feedback(request_id, positive, feedback_msg)

def display_feedback(request_id):
    feedback_key = f"feedback_message_{request_id}"

    # Initialize session state for the text input
    if feedback_key not in st.session_state:
        st.session_state[feedback_key] = ""

    # Popover for compact UI
    with st.popover("📝 Give Feedback"):
        st.write(f"Request ID: `{request_id}`")
        
        # Optional message box
        st.text_input(
            label="💬 What did you think?",
            key=feedback_key,
            placeholder="Let us know what worked or what could be better..."
        )

        # Thumbs up/down buttons
        col1, col2, col3, col4, col5 = st.columns([2, 2, 1, 1, 2])
        with col3:
            st.button(
                ":thumbsup:",
                on_click=submit_feedback_with_message,
                args=(request_id, "true", feedback_key),
                key=f"thumbsup_{request_id}"
            )
        with col4:
            st.button(
                ":thumbsdown:",
                on_click=submit_feedback_with_message,
                args=(request_id, "false", feedback_key),
                key=f"thumbsdown_{request_id}"
            )


def get_raw_connection(conn_name: str = "demo_account", conn_type: str = "snowflake"):
    """
    Returns the raw underlying connection object from Streamlit's st.connection().
    """
    conn = st.connection(conn_name, type=conn_type)
    raw_conn = getattr(conn, "_instance", None)
    if raw_conn is None:
        raise ValueError("Could not access underlying connection instance.")
    return raw_conn

def trim_and_get_unique(series: pd.Series) -> list:
    """Trims whitespace from each string in the series and returns unique values as a list."""
    return series.str.strip().unique().tolist()    


            
def submit_feedback(request_id: str, positive: bool, feedback_message: str) -> Optional[str]:
    request_body = {
        "request_id": request_id,
        "positive": positive,
        "feedback_message": feedback_message
    }

    url = f"https://{st.session_state.RAW_CONN.host}/api/v2/cortex/analyst/feedback"

    # logger.info("Submitting feedback...")
    # logger.info(f"URL: {url}")
    # logger.info(f"Request Body: {request_body}")

    # st.code(f"Submitting feedback to: {url}")
    # st.json(request_body)

    try:
        resp = requests.post(
            url=url,
            json=request_body,
            headers={
                "Authorization": f'Snowflake Token="{st.session_state.RAW_CONN.rest.token}"',
                "Content-Type": "application/json",
            }
        )
    except Exception as e:
        logger.error(f"Request failed: {e}")
        st.error(f"❌ Request failed: {e}")
        return f"❌ Request failed with exception: {e}"

    logger.info(f"Response code: {resp.status_code}")
    logger.info(f"Response text: {resp.text}")


    if resp.status_code == 200:
        logger.info("✅ Feedback submitted successfully.")
        return None

    try:
        parsed = resp.json()
    except Exception:
        parsed = {
            "request_id": request_id,
            "error_code": "Unknown",
            "message": "Unable to parse response content",
        }

    err_msg = f"""
    🚨 Analyst API error 🚨  
    - response code: `{resp.status_code}`  
    - request-id: `{parsed.get('request_id')}`  
    - error code: `{parsed.get('error_code')}`  
    
    **Message:**  
    {parsed.get('message')}
    """
    logger.error("Feedback submission failed:")
    logger.error(err_msg)

    st.error(err_msg)
    return err_msg

    
                     

# try:
#     raw_conn = get_raw_connection()
#     token = raw_conn.rest.token
# except Exception as e:
#     st.error(f"Failed to access token: {e}")

# if "RAW_CONN" not in st.session_state:
#     st.session_state.RAW_CONN = get_raw_connection()    
    
# st.write(st.session_state.RAW_CONN.host)
# st.write(st.session_state.RAW_CONN.account)
# st.write(st.session_state.RAW_CONN.database)    
    
def get_user_history(user):   
    prev_questions=[] 
    # df=pd.read_sql("select current_user()",st.session_state.RAW_CONN)
    # user = df.iloc[0, 0]
    login=f"""SELECT * FROM table(SNOWFLAKE.LOCAL.CORTEX_ANALYST_REQUESTS('FILE_ON_STAGE', '@{st.session_state.RAW_CONN.database}.{st.session_state.RAW_CONN.schema}.{STAGE}/{FILE}')) where user_name= '{user}'"""
    df=pd.read_sql(login,st.session_state.RAW_CONN)        
    df["DATE"] = pd.to_datetime(df["TIMESTAMP"]).dt.date
    # # prev questions group by date code
    # grouped = df.groupby("DATE")["LATEST_QUESTION"].apply(trim_and_get_unique).reset_index()
    # for _, row in questions.iterrows():            
    #     for idx, question in enumerate(row["LATEST_QUESTION"]):
    #         prev_questions.append(question)
    # prev questions
    # prev_questions = df["LATEST_QUESTION"].dropna().apply(lambda x: x.strip()).unique().tolist()
    prev_questions = df["LATEST_QUESTION"].dropna().apply(lambda x: x.strip()).unique()
    
    #monitoring code
    selected_columns = ["TIMESTAMP","LATEST_QUESTION", "FEEDBACK", "WARNINGS"]            
    monitoring_df = df[selected_columns]
    monitoring_df = monitoring_df.rename(columns={
    "LATEST_QUESTION": "QESTIONS"
    })    
    return user,prev_questions,monitoring_df
    

# def history_callback():    
#     st.session_state.selected_question = st.session_state.selected_question
#     user_input = st.chat_input(st.session_state.selected_question)
#     if 'selected_question' in st.session_state.selected_question:            
#         process_message(prompt=user_input)    

def history_sidebar(user,prev_questions,monitoring_df):
    with st.sidebar:
        # st.markdown(f"**Logged in as : {user}**", unsafe_allow_html=True)
        col1,col2 = st.tabs(["Historical","Monitoring"])        
        # with expander
        # with col1.expander("Previous Questions"):                    
        #     for q in prev_questions:
        #         st.markdown(f"- {q}")  
        # with col2.expander("Monitoring"):                    
        #     st.write(monitoring_df)            
        # without expander
        with col1:
            # st.dataframe(st.data_editor(prev_questions, num_rows="dynamic"),hide_index=True)    
            for question in prev_questions:
                st.markdown(f"- {question}")
            # for question in prev_questions:
            #     clicked_question =  st.sidebar.button(question)
            # if clicked_question:
            #         st.session_state.question = question
            #         st.session_state.show_text_input = True                  
        with col2:
            # st.data_editor(monitoring_df, num_rows="dynamic")    
            st.dataframe(monitoring_df, hide_index=True)
                
                    
### Main starts here
st.markdown("""
    <style>
    .custom-title {
        background-color: #000098;
        color: white;
        padding: 15px;
        border-radius: 10px;
        font-size: 32px;
        font-weight: bold;
        text-align: left;
        margin-bottom: 20px;
    }
    .custom-logo{
            margin-bottom: 20px;
            font-size: 32px;
        font-weight: bold;
        color: #000098;
        text-align: left;
        align-items: center;
        display:flex;
            }
    /* Styling for chat input */
    .chat-input-container {
        display: flex;
        align-items: center;
        border: 2px solid #ccc;
        border-radius: 25px;
        padding: 5px 10px;
    }
    .chat-input-container input {
        border: none;
        outline: none;
        flex: 1;
        font-size: 16px;
        padding: 10px;
    }
    /* Submit button with custom background image */
    div.stFormSubmitButton > button {
        background-color: #000098;
        color: white;
        border: none;
        border-radius: 50%;
        width: 50px;
        height: 50px;
        font-weight: bold;
        font-size: 18px;
        cursor: pointer;
        background-image: url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzEiIHZpZXdCb3g9IjAgMCAzMiAzMSIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4NCjxyZWN0IHdpZHRoPSIzMS40MzQ0IiBoZWlnaHQ9IjMxIiByeD0iMTUuNSIgZmlsbD0iIzAwMDA5OSIvPg0KPHBhdGggZD0iTTEwLjkwNzUgMjAuODcxM0MxMS4zNzg2IDIwLjg3MTMgMTEuNzYwNSAyMC40OTk0IDExLjc2MDUgMjAuMDQwNkMxMS43NjA1IDE5LjU4MTkgMTEuMzc4NiAxOS4yMSAxMC45MDc1IDE5LjIxQzEwLjQzNjMgMTkuMjEgMTAuMDU0NCAxOS41ODE5IDEwLjA1NDQgMjAuMDQwNkMxMC4wNTQ0IDIwLjQ5OTQgMTAuNDM2MyAyMC44NzEzIDEwLjkwNzUgMjAuODcxM1oiIGZpbGw9IndoaXRlIi8+DQo8cGF0aCBkPSJNMTIuODY2MSAxOC45NjQxQzEzLjMzNzIgMTguOTY0MSAxMy43MTkxIDE4LjU5MjIgMTMuNzE5MSAxOC4xMzM0QzEzLjcxOTEgMTcuNjc0NiAxMy4zMzcyIDE3LjMwMjcgMTIuODY2MSAxNy4zMDI3QzEyLjM5NDkgMTcuMzAyNyAxMi4wMTMgMTcuNjc0NiAxMi4wMTMgMTguMTMzNEMxMi4wMTMgMTguNTkyMiAxMi4zOTQ5IDE4Ljk2NDEgMTIuODY2MSAxOC45NjQxWiIgZmlsbD0id2hpdGUiLz4NCjxwYXRoIGQ9Ik0xNS4wMzQ1IDE2Ljk4MDdDMTUuNTA1NyAxNi45ODA3IDE1Ljg4NzYgMTYuNjA4OCAxNS44ODc2IDE2LjE1QzE1Ljg4NzYgMTUuNjkxMiAxNS41MDU3IDE1LjMxOTMgMTUuMDM0NSAxNS4zMTkzQzE0LjU2MzQgMTUuMzE5MyAxNC4xODE1IDE1LjY5MTIgMTQuMTgxNSAxNi4xNUMxNC4xODE1IDE2LjYwODggMTQuNTYzNCAxNi45ODA3IDE1LjAzNDUgMTYuOTgwN1oiIGZpbGw9IndoaXRlIi8+DQo8cGF0aCBkPSJNMTYuNzkxIDE1LjEzNEMxNy4yNjIyIDE1LjEzNCAxNy42NDQxIDE0Ljc2MjEgMTcuNjQ0MSAxNC4zMDMzQzE3LjY0NDEgMTMuODQ0NiAxNy4yNjIyIDEzLjQ3MjcgMTYuNzkxIDEzLjQ3MjdDMTYuMzE5OSAxMy40NzI3IDE1LjkzOCAxMy44NDQ2IDE1LjkzOCAxNC4zMDMzQzE1LjkzOCAxNC43NjIxIDE2LjMxOTkgMTUuMTM0IDE2Ljc5MSAxNS4xMzRaIiBmaWxsPSJ3aGl0ZSIvPg0KPHBhdGggZD0iTTE4Ljc1NzQgMTMuMjI2OEMxOS4yMjg2IDEzLjIyNjggMTkuNjEwNSAxMi44NTQ5IDE5LjYxMDUgMTIuMzk2MUMxOS42MTA1IDExLjkzNzMgMTkuMjI4NiAxMS41NjU0IDE4Ljc1NzQgMTEuNTY1NEMxOC4yODYzIDExLjU2NTQgMTcuOTA0NCAxMS45MzczIDE3LjkwNDQgMTIuMzk2MUMxNy45MDQ0IDEyLjg1NDkgMTguMjg2MyAxMy4yMjY4IDE4Ljc1NzQgMTMuMjI2OFoiIGZpbGw9IndoaXRlIi8+DQo8cGF0aCBkPSJNMjEuMzkyMiAxMC42NjEzQzIxLjg2MzMgMTAuNjYxMyAyMi4yNDUzIDEwLjI4OTQgMjIuMjQ1MyA5LjgzMDY3QzIyLjI0NTMgOS4zNzE5MSAyMS44NjMzIDkgMjEuMzkyMiA5QzIwLjkyMTEgOSAyMC41MzkyIDkuMzcxOTEgMjAuNTM5MiA5LjgzMDY3QzIwLjUzOTIgMTAuMjg5NCAyMC45MjExIDEwLjY2MTMgMjEuMzkyMiAxMC42NjEzWiIgZmlsbD0id2hpdGUiLz4NCjxwYXRoIGQ9Ik0xOC43NTc0IDEwLjY2MTNDMTkuMjI4NiAxMC42NjEzIDE5LjYxMDUgMTAuMjg5NCAxOS42MTA1IDkuODMwNjdDMTkuNjEwNSA5LjM3MTkxIDE5LjIyODYgOSAxOC43NTc0IDlDMTguMjg2MyA5IDE3LjkwNDQgOS4zNzE5MSAxNy45MDQ0IDkuODMwNjdDMTcuOTA0NCAxMC4yODk0IDE4LjI4NjMgMTAuNjYxMyAxOC43NTc0IDEwLjY2MTNaIiBmaWxsPSJ3aGl0ZSIvPg0KPHBhdGggZD0iTTE2LjEyMjYgMTAuNjYxM0MxNi41OTM4IDEwLjY2MTMgMTYuOTc1NyAxMC4yODk0IDE2Ljk3NTcgOS44MzA2N0MxNi45NzU3IDkuMzcxOTEgMTYuNTkzOCA5IDE2LjEyMjYgOUMxNS42NTE1IDkgMTUuMjY5NiA5LjM3MTkxIDE1LjI2OTYgOS44MzA2N0MxNS4yNjk2IDEwLjI4OTQgMTUuNjUxNSAxMC42NjEzIDE2LjEyMjYgMTAuNjYxM1oiIGZpbGw9IndoaXRlIi8+DQo8cGF0aCBkPSJNMTMuNDg3OCAxMC42NjEzQzEzLjk1OSAxMC42NjEzIDE0LjM0MDkgMTAuMjg5NCAxNC4zNDA5IDkuODMwNjdDMTQuMzQwOSA5LjM3MTkxIDEzLjk1OSA5IDEzLjQ4NzggOUMxMy4wMTY3IDkgMTIuNjM0OCA5LjM3MTkxIDEyLjYzNDggOS44MzA2N0MxMi42MzQ4IDEwLjI4OTQgMTMuMDE2NyAxMC42NjEzIDEzLjQ4NzggMTAuNjYxM1oiIGZpbGw9IndoaXRlIi8+DQo8cGF0aCBkPSJNMTAuODUzMSAxMC42NjEzQzExLjMyNDIgMTAuNjYxMyAxMS43MDYxIDEwLjI4OTQgMTEuNzA2MSA5LjgzMDY3QzExLjcwNjEgOS4zNzE5MSAxMS4zMjQyIDkgMTAuODUzMSA5QzEwLjM4MTkgOSAxMCA5LjM3MTkxIDEwIDkuODMwNjdDMTAgMTAuMjg5NCAxMC4zODE5IDEwLjY2MTMgMTAuODUzMSAxMC42NjEzWiIgZmlsbD0id2hpdGUiLz4NCjxwYXRoIGQ9Ik0yMS4zOTIyIDEzLjIyNjhDMjEuODYzMyAxMy4yMjY4IDIyLjI0NTMgMTIuODU0OSAyMi4yNDUzIDEyLjM5NjFDMjIuMjQ1MyAxMS45MzczIDIxLjg2MzMgMTEuNTY1NCAyMS4zOTIyIDExLjU2NTRDMjAuOTIxMSAxMS41NjU0IDIwLjUzOTIgMTEuOTM3MyAyMC41MzkyIDEyLjM5NjFDMjAuNTM5MiAxMi44NTQ5IDIwLjkyMTEgMTMuMjI2OCAyMS4zOTIyIDEzLjIyNjhaIiBmaWxsPSJ3aGl0ZSIvPg0KPHBhdGggZD0iTTIxLjM5MjIgMTUuNzkyMkMyMS44NjMzIDE1Ljc5MjIgMjIuMjQ1MyAxNS40MjAzIDIyLjI0NTMgMTQuOTYxNUMyMi4yNDUzIDE0LjUwMjggMjEuODYzMyAxNC4xMzA5IDIxLjM5MjIgMTQuMTMwOUMyMC45MjExIDE0LjEzMDkgMjAuNTM5MiAxNC41MDI4IDIwLjUzOTIgMTQuOTYxNUMyMC41MzkyIDE1LjQyMDMgMjAuOTIxMSAxNS43OTIyIDIxLjM5MjIgMTUuNzkyMloiIGZpbGw9IndoaXRlIi8+DQo8cGF0aCBkPSJNMjEuMzkyMiAxOC4zNTg2QzIxLjg2MzMgMTguMzU4NiAyMi4yNDUzIDE3Ljk4NjcgMjIuMjQ1MyAxNy41Mjc5QzIyLjI0NTMgMTcuMDY5MiAyMS44NjMzIDE2LjY5NzMgMjEuMzkyMiAxNi42OTczQzIwLjkyMTEgMTYuNjk3MyAyMC41MzkyIDE3LjA2OTIgMjAuNTM5MiAxNy41Mjc5QzIwLjUzOTIgMTcuOTg2NyAyMC45MjExIDE4LjM1ODYgMjEuMzkyMiAxOC4zNTg2WiIgZmlsbD0id2hpdGUiLz4NCjxwYXRoIGQ9Ik0yMS4zOTIyIDIwLjkyNEMyMS44NjMzIDIwLjkyNCAyMi4yNDUzIDIwLjU1MjEgMjIuMjQ1MyAyMC4wOTM0QzIyLjI0NTMgMTkuNjM0NiAyMS44NjMzIDE5LjI2MjcgMjEuMzkyMiAxOS4yNjI3QzIwLjkyMTEgMTkuMjYyNyAyMC41MzkyIDE5LjYzNDYgMjAuNTM5MiAyMC4wOTM0QzIwLjUzOTIgMjAuNTUyMSAyMC45MjExIDIwLjkyNCAyMS4zOTIyIDIwLjkyNFoiIGZpbGw9IndoaXRlIi8+DQo8L3N2Zz4NCg=='); /* Set the icon as background */
        background-size: cover;  /* Ensures the image covers the button */
        background-position: center; /* Centers the image in the button */
    }
    div.stFormSubmitButton > button:hover {
        background-color: #000070;
    }
    </style>
""", unsafe_allow_html=True)

# st.markdown('<div class="custom-title"><img src="data:image/png;base64,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" alt="skailogo" style="width: 34px; margin-right: 7px;">Ask Prism(RAAS)💬</div>', unsafe_allow_html=True)
st.markdown('<div class="custom-title">Ask Prism(RAAS)💬</div>', unsafe_allow_html=True)

# st.title("💬Ask PRISM(RAAS)")

# st.sidebar.markdown(f'<div class="custom-logo"><img src="data:image/png;base64,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" alt="skailogo" style="width: 70px; margin-right: 7px;"> <div style="font-size: 22px;font-weight: bold;color: #2e7d32;"> {user}</div></div>', unsafe_allow_html=True)

st.sidebar.markdown(f"""<div class="custom-logo"><img src="data:image/png;base64,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" alt="skailogo" style="width: 100px; margin-right: 7px;"><div><div style=font-size:14px;>Welcome </div><div style=font-size:18px;>{user}</div></div></div>""", unsafe_allow_html=True)

# Initialize/reset session state
if st.button("Reset conversation") or "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.status = "Interpreting question"
    st.session_state.error = None
    st.session_state.form_submitted = ({}) 
    st.session_state.question = ""
    
if 'question' not in st.session_state:
    st.session_state.question = ""
    
if "show_text_input" not in st.session_state:
    st.session_state.show_text_input = False      
   

user,prev_questions,monitoring_df=get_user_history(user) 
history_sidebar(user,prev_questions,monitoring_df)

try:    
    show_conversation_history() 
except:
    st.info("Something went wrong. To get back on track, please reset the conversation.")  
    # if st.button("Reset conversation") or "messages" not in st.session_state:
    #     st.session_state.messages = []
    #     st.session_state.status = "Interpreting question"
    #     st.session_state.error = None
    #     st.session_state.form_submitted = ({}) 
    #     st.session_state.selected_question = ""    
    
    
# if st.session_state.show_text_input:
#     with st.form(key="chat_form", clear_on_submit=True):
#         col1, col2 = st.columns([10, 1])
#         with col1:
#             print(st.session_state.get("question", "none"))
#             user_input = st.text_input("", key="user_input_form", value=st.session_state.get("question", ""), label_visibility="collapsed", placeholder="Type your message...")
#         with col2:
#         # Submit button with custom image as background
#             clicked = st.form_submit_button("")  # Empty label, styling comes from CSS
#         if clicked and user_input.strip():
#             st.session_state.show_text_input = False
#             process_message(user_input)                          
        
user_input = st.chat_input("What is your question?")
if user_input:    
    try:
        process_message(prompt=user_input)
    except:
        st.info("Something went wrong. To get back on track, please reset the conversation.")  
        # if st.button("Reset conversation") or "messages" not in st.session_state:
        #     st.session_state.messages = []
        #     st.session_state.status = "Interpreting question"
        #     st.session_state.error = None
        #     st.session_state.form_submitted = ({}) 
        #     st.session_state.selected_question = ""       
            
            

 
# Check for user input from sidebar click or chat input
# value=st.session_state.get("selected_question", "")
# if value:
#     user_input = st.text_input("", key="user_input_form", value=value, label_visibility="collapsed", placeholder="Type your message...")    
#     try:
#         process_message(prompt=user_input)
#     except:
#         st.info("Something went wrong. To get back on track, please reset the conversation.")  
        # if st.button("Reset conversation") or "messages" not in st.session_state:
        #     st.session_state.messages = []
        #     st.session_state.status = "Interpreting question"
        #     st.session_state.error = None
        #     st.session_state.form_submitted = ({}) 
        #     st.session_state.selected_question = ""   
# else:
#     user_input = st.chat_input("What is your question?")
#     try:
#         process_message(prompt=user_input)
#     except:
#         st.info("Something went wrong. To get back on track, please reset the conversation.")  
        # if st.button("Reset conversation") or "messages" not in st.session_state:
        #     st.session_state.messages = []
        #     st.session_state.status = "Interpreting question"
        #     st.session_state.error = None
        #     st.session_state.form_submitted = ({}) 
        #     st.session_state.selected_question = ""   

     