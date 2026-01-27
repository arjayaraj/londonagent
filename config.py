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

import os
import logging
from pydantic_settings import BaseSettings
from pydantic import BaseModel, Field, ValidationError
from google.adk.sessions import InMemorySessionService


logger = logging.getLogger(__name__)


MAX_NUM_ROWS = os.getenv('MAX_NUM_ROWS', 20)
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", 'text-embedding-005')
DEBUG_STATE = os.getenv("DEBUG_STATE", "false").lower() in ('true', '1', 't', 'yes', 'y')
EMBEDDING_DIMENSION = 768
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", 'gemini-2.5-flash')
PROJECT_ID= os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION=os.getenv("GOOGLE_CLOUD_LOCATION")

# Database Configuration
DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()
SQLLITE_DB_PATH = os.getenv('SQLITE_DB_PATH', "../data_london/")
POSTGRES_USER = os.getenv("POSTGRES_USER", "user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "password")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "LONDON_travel")

logger.info(f"The DB type is: {DB_TYPE}")



session_service = InMemorySessionService()

class AgentModel(BaseModel):
    """Agent model settings."""
    name: str = Field(default="london_holiday_agent")
    model: str = Field(default=LLM_MODEL_NAME)

class Config(BaseSettings):
    """Configuration settings for the london holiday agent."""

    # Database settings
    db_type: str = DB_TYPE
    postgres_user: str = POSTGRES_USER or "user"
    postgres_password: str = POSTGRES_PASSWORD or "password"
    postgres_host: str = POSTGRES_HOST or "localhost"
    postgres_port: str = POSTGRES_PORT or "5432"
    postgres_db: str = POSTGRES_DB or "london_activities"

    db_file_path: str = os.path.join(SQLLITE_DB_PATH, "london_travel.sql")
    embedding_model_name: str = EMBEDDING_MODEL_NAME
    max_rows: int = MAX_NUM_ROWS
    debug_state:bool = DEBUG_STATE
    project: str = PROJECT_ID
    location:str = LOCATION
    app_name: str = "LYLA"
    agent_settings: AgentModel = Field(default_factory=AgentModel) 
    genai_use_vertexai: str = Field(default="1") 

try:
    configs = Config()
except ValidationError as e:
    logger.error(
        f"Pydantic ValidationError loading configuration in config.py. "
        f"Details: {e.errors()}"
    )
except Exception as e:
    logger.error(f"Unexpected error loading configuration in config.py: {e}")
