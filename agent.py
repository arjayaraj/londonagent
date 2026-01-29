# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from .config import Config
from .prompts import return_instructions_lyla
from .sub_agents.search_agent.tools import setup_sqlite_client
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from .tools.tools import call_db_agent
from google.genai import types


import os
import google.auth
from google.auth.transport.requests import Request


from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams, SseConnectionParams

# --- IMPORT MODEL ARMOR MODULE ---
from .sub_agents.search_agent.model_armor import (
    check_model_input,
    check_model_output,
    check_tool_output
)

# --- BIGQUERY MCP TOOL ---
# This imports the pre-initialized tool from your new file
from .sub_agents.search_agent.bigquery_mcp import bigquery_toolset

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
APP_NAME = "LYLA"
configs = Config()

def setup_db_resources(callback_context: CallbackContext):
    setup_sqlite_client()

active_tools = [t for t in [call_db_agent, bigquery_toolset] if t is not None]

lyla_agent = Agent(
    model=configs.agent_settings.model,
    instruction=return_instructions_lyla(),
    name=configs.agent_settings.name,


    tools=active_tools,
    
    before_agent_callback=setup_db_resources,
    
    # Attach Security Callbacks 
    before_model_callback=check_model_input,
    after_model_callback=check_model_output,
    after_tool_callback=check_tool_output,
    
    generate_content_config=types.GenerateContentConfig(temperature=0.01),
)

root_agent = lyla_agent