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

"""This file contains the tools used by the database agent."""

import logging
import re
import requests
import json
import asyncio
import os
import time
from typing import Any, Dict, List, Optional

# Conditional imports to handle local development environments where strict dependencies might be missing
try:
    from pysqlite3 import dbapi2 as sqlite3
    import sqlite_vec
except ImportError:
    import sqlite3
    # Fallback or warning if sqlite_vec is missing, though it is in requirements

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from pgvector.psycopg2 import register_vector
except ImportError:
    psycopg2 = None

from pydantic import BaseModel
from google import genai
from google.adk.tools import ToolContext
from google.genai import Client
from ...config import Config
from ...utils import write_to_tool_context

logger = logging.getLogger(__name__)

configs = Config()
base_dir = os.path.dirname(os.path.abspath(__name__))

llm_client = Client(vertexai=True, project=configs.project, location=configs.location)

# Global variables to store database settings and connections
database_settings = None
_sqlite_conn = None
_postgres_conn = None

class activity(BaseModel):
    activity_id: str
    name: str
    description: str
    cost: float
    duration_min: int
    duration_max: int
    kid_friendliness_score: int

class SQL_query_output(BaseModel):
    sql_query: str
    justification: str

class actvities_search_output(BaseModel):
    activities_list: list[activity] | None = None
    error_message: str

def setup_sqlite_client():
    """
    Establishes and returns a SQLite database connection.
    If the database file does not exist, it copies it from a default location.
    Ensures the sqlite-vec extension is loaded.
    """
    global _sqlite_conn
    if _sqlite_conn is None:
        try:
            if not os.path.exists(configs.db_file_path):
                raise Exception(f"Database not found at {configs.db_file_path}")
            
            # load the data in configs.db_file_path which is a .sql file to the sqllite db
            with open(configs.db_file_path, 'r') as f:
                sql_script = f.read()
            
            _sqlite_conn = sqlite3.connect(":memory:")
            temp_cursor = _sqlite_conn.cursor()
            temp_cursor.executescript(sql_script)
            logger.info(f"SQLite Database created and loaded from {configs.db_file_path}")
            
            try:
                _sqlite_conn.enable_load_extension(True)
                sqlite_vec.load(_sqlite_conn)
                logger.info("sqlite-vec extension loaded.")
            except Exception as e:
                logger.warning(f"Failed to load sqlite-vec extension: {e}")

            temp_cursor.close()
            return _sqlite_conn
        except Exception as e:
            logger.error(f"Error connecting to SQLite: {e}")
            if _sqlite_conn:
                _sqlite_conn.close()
            _sqlite_conn = None
            return None
    else:
        return _sqlite_conn

def setup_postgres_client():
    """
    Establishes and returns a Postgres database connection.
    Ensures the vector extension is loaded and data is initialized.
    """
    global _postgres_conn
    
    if not psycopg2:
        logger.error("psycopg2 module not found. Cannot connect to Postgres.")
        return None

    # Check validity of existing connection
    if _postgres_conn is not None:
        try:
            cur = _postgres_conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return _postgres_conn
        except psycopg2.Error:
            logger.info("Existing Postgres connection closed or invalid. Reconnecting...")
            _postgres_conn = None

    try:
        conn = psycopg2.connect(
            host=configs.postgres_host,
            port=configs.postgres_port,
            user=configs.postgres_user,
            password=configs.postgres_password,
            dbname=configs.postgres_db
        )
        conn.autocommit = True
        
        # Enable pgvector extension
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            register_vector(conn)
        
        # Check if data exists
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.activities');")
            table_exists = cur.fetchone()[0]
            
            if not table_exists:
                logger.info("Initializing Postgres database from SQL file...")
                if os.path.exists(configs.db_file_path):
                    with open(configs.db_file_path, 'r') as f:
                        sql_lines = f.readlines()
                    
                    # Filter out SQLite specific commands and execute
                    filtered_sql = []
                    for line in sql_lines:
                        if line.strip().upper().startswith("PRAGMA"):
                            continue
                        filtered_sql.append(line)
                    
                    full_sql = "".join(filtered_sql)
                    cur.execute(full_sql)
                    logger.info("Postgres database initialized.")
                else:
                    logger.error(f"SQL file not found at {configs.db_file_path}")
            else:
                logger.info("Postgres activities table already exists.")

        _postgres_conn = conn
        return _postgres_conn
    except Exception as e:
        logger.error(f"Error connecting to Postgres: {e}")
        return None

def get_db_connection():
    """Wrapper to get appropriate DB connection based on config."""
    if configs.db_type == "postgres":
        return setup_postgres_client()
    else:
        return setup_sqlite_client()

def get_database_settings():
    """
    Retrieves and returns database settings.
    """
    global database_settings
    if database_settings is None:
        if configs.db_type == "postgres":
             database_settings = {
                "sqlite_ddl_schema": """
                TABLE activities (
                    activity_id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    duration_min INT,
                    duration_max INT,
                    kid_friendliness_score INT,
                    cost INT,
                    sight_id VARCHAR(50) REFERENCES locations(sight_id), -- Foreign key to locations table
                    description TEXT,
                    embedding VECTOR(768)
                );
                """
            }
        else:
            database_settings = {
                "sqlite_ddl_schema": """
                TABLE activities (
                    activity_id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    duration_min INT,
                    duration_max INT,
                    kid_friendliness_score INT,
                    cost INT,
                    sight_id VARCHAR(50) REFERENCES locations(sight_id), -- Foreign key to locations table
                    description TEXT,
                    embedding VECTOR(768)
                );
                """
            }
    return database_settings


async def get_embedding_tool(
    vector_query: str,
    tool_context: ToolContext = None,
) -> list[float]:
    """Tool to create vector embedding for the vector search components of the user's query."""
    try:
        write_to_tool_context("get_embedding_tool_input", vector_query, tool_context)
        client = genai.Client()
        response = client.models.embed_content(
            model=configs.embedding_model_name,
            contents=vector_query,
        )
        if configs.debug_state:
            write_to_tool_context("get_embedding_tool_output", response.embeddings[0].values, tool_context)
        return response.embeddings[0].values
    except requests.exceptions.RequestException as e:
        logger.error(f"Error generating embedding for text '{vector_query}': {e}")
        write_to_tool_context("get_embedding_tool_error", f"Error generating embedding for text '{vector_query}': {e}", tool_context)
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON response for text '{vector_query}': {e}")
        write_to_tool_context("get_embedding_tool_error", f"Error decoding JSON response for text '{vector_query}': {e}", tool_context)
        return None


async def get_activities_tool(
    vector_query: str,
    keyword_queries: str,
    tool_context: ToolContext=None,
) -> str:
    write_to_tool_context("get_activities_tool_input", {"vector_query": vector_query, "keyword_queries": keyword_queries}, tool_context)
    embedding = None
    if vector_query != "":
        embedding = await get_embedding_tool(vector_query, tool_context)

    where_clause = await get_sql_where_clause_tool(keyword_queries, tool_context)

    sql_query = ""
    params = []

    if configs.db_type == "postgres":
        sql_query += "SELECT activity_id, name, description, cost, duration_min, duration_max, kid_friendliness_score, sight_id "
        
        if embedding:
            # Postgres pgvector syntax using cosine distance operator <=>
            # Note: We need to cast the embedding array string to vector type if handled as string, 
            # but psycopg2 with register_vector handles list directly if passed as param.
            # However, here we are constructing SQL string.
            # To keep it simple and consistent with how other parts might work, let's inject valid SQL syntax
            # pgvector distance: embedding <=> '[...]'
            sql_query += f", (embedding <=> '{embedding}') AS score "
        else:
            sql_query += ", 0 AS score " # Default score if no embedding

        sql_query += "FROM activities "
        
        if where_clause:
            sql_query += f"WHERE {where_clause} "
        
        sql_query += f"ORDER BY score ASC LIMIT {configs.max_rows};"

    else:
        # SQLite with sqlite-vec
        sql_query += "SELECT activity_id, name, description, cost, duration_min, duration_max, kid_friendliness_score, sight_id "
        if embedding:
            sql_query+= f", vec_distance_cosine(embedding, vec_f32('{embedding}')) AS score "
        else:
            sql_query += ", 0 AS score "
        
        sql_query += "FROM activities "
        if where_clause:
            sql_query += f"WHERE {where_clause} "
        
        sql_query += f"ORDER BY score ASC LIMIT {configs.max_rows};"

    if configs.debug_state:
        write_to_tool_context("get_data_tool_sql_query", sql_query, tool_context)

    results = await get_data_from_db_tool(sql_query, params, tool_context)

    if not results:
        return str(actvities_search_output(activities_list=None, error_message="Failed to get results."))

    return str(results)


async def get_sql_where_clause_tool(
    keyword_queries: str,
    tool_context: ToolContext=None,
) -> str:
    """Generates an initial SQL WHERE clause from a natural language question."""

    get_database_settings()
    write_to_tool_context("get_sql_where_clause_tool_input", keyword_queries, tool_context)

    prompt_template = """
    You are an AI assistant serving as an expert in converting keywords into the WHERE clauses of the **SQL queries**.
    Your primary goal is to take keywords (eg: "duration <= 3 days") that express their travel constraints and translate into a WHERE clause of a SQL query.
    The schema of the db is given below.

    You must produce your final response as a JSON format with the following four keys:
    - "justification": A step-by-step reasoning explaining how you generated the SQL query based on the schema, examples, and the input.
    - "where": The where clause of the query

    **Important Directives:**
    -   **Schema Adherence**: Strictly adhere to the provided database schema creating the sql_query.

    **Schema:**
    The database structure is defined by the following table schemas (possibly with sample rows):
    ```
    {SCHEMA}
    ```
    duration_min and duration_max are expressed in minutes
    cost is expressed in euros

    Query Parameters that need to be translated into SQL

    ```
    {QUERY_PARAMS}
    ```

    **Think Step-by-Step:** Carefully consider the schema and keyword queries, and all guidelines to generate and validate the correct SQL.
    """

    ddl_schema = database_settings.get("sqlite_ddl_schema", "")
    if not ddl_schema:
        logger.warning("Database schema is not available.")

    try:
        prompt = prompt_template.format(
            MAX_NUM_ROWS=configs.max_rows, SCHEMA=ddl_schema, QUERY_PARAMS=keyword_queries
        )

        response = llm_client.models.generate_content(
            model=configs.agent_settings.model,
            contents=prompt,
            config={"temperature": 0.1},
        )

        response_text = response.text
        if configs.debug_state:
            write_to_tool_context("get_sql_where_clause_llm_prompt", prompt, tool_context)

        json_text = response_text.replace("```json", "").replace("```", "").strip()
        json_obj = json.loads(json_text)
        where_clause = json_obj.get("where", "")
        if configs.debug_state:
            write_to_tool_context("get_sql_where_clause_output", where_clause, tool_context)
        return where_clause
    except Exception as e:
        logger.error(f"Error generating SQL: {e}")
        write_to_tool_context("get_sql_where_clause_error", f"Error generating SQL: {e}", tool_context)
        return None


async def get_data_from_db_tool(
    sql_string: str,
    params: list,
    tool_context: ToolContext = None,
) -> actvities_search_output:
    """
    Validates SQL syntax and functionality by executing it against the database.
    """

    output = actvities_search_output(error_message="")

    logger.debug("Validating SQL: %s", sql_string)

    if re.search(
        r"(?i)\b(update|delete|drop|insert|create|alter|truncate|merge)\b", sql_string
    ):
        output.error_message = "Invalid SQL: Contains disallowed DML/DDL operations."
        return output

    conn = null_conn = None
    cur = None
    try:
        conn = get_db_connection()
        if not conn:
            raise ConnectionError(f"Failed to establish {configs.db_type} connection.")

        # Cursor creation diffs
        if configs.db_type == "postgres":
            cur = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cur = conn.cursor()

        cur.execute(sql_string, params)

        formatted_rows = []
        
        if configs.db_type == "postgres":
            # RealDictCursor returns dict-like objects
            db_rows = cur.fetchall()
            for row in db_rows:
                formatted_rows.append(dict(row))
        else:
            # SQLite default cursor returns tuples
            if cur.description:
                column_names = [desc[0] for desc in cur.description]
                db_rows = cur.fetchall()
                for row_data in db_rows:
                    formatted_rows.append(dict(zip(column_names, row_data)))

        activities_list = []
        try:
            for row in formatted_rows:
                # Handle potential key differences or casing if any
                activity_obj = activity(
                    activity_id=row.get('activity_id'),
                    name=row.get('name'),
                    description=row.get('description'),
                    cost=row.get('cost'),
                    duration_min=row.get('duration_min'),
                    duration_max=row.get('duration_max'),
                    kid_friendliness_score=row.get('kid_friendliness_score')
                )
                activities_list.append(activity_obj)
            
            logger.info(f"Number of activities returned: {len(activities_list)}")

            output.activities_list = activities_list
            if configs.debug_state and tool_context is not None:
                write_to_tool_context("get_data_from_db_tool_output", output.activities_list, tool_context)
        except Exception as format_error:
            output.error_message = f"Error formatting row into activity object: {format_error}"
            write_to_tool_context("get_data_from_db_tool_error", output.error_message, tool_context)
            logger.error(output.error_message)

    except (sqlite3.Error, psycopg2.Error) as e:
        output.error_message = f"Invalid SQL: Database error - {e}"
        logger.error(output.error_message)
    except ConnectionError as e:
        output.error_message = f"Database Connection Error: {e}"
        logger.error(output.error_message)
    except Exception as e:
        output.error_message = f"Invalid SQL: An unexpected error occurred - {e}"
        logger.error(output.error_message)
    finally:
        write_to_tool_context("get_data_from_db_tool_error", output.error_message, tool_context)
        if cur:
            cur.close()
        # For Postgres we typically leave connection open or pooled, but here we might close if not global?
        # Current logic reuses global, so don't close conn.

    return output


if __name__ == "__main__":
    logger.info("Initializing database tools...")
    get_database_settings()
    conn = get_db_connection()
    if conn:
        logger.info(f"Test connection successful ({configs.db_type}).")
    else:
        logger.info("Test connection failed.")
    asyncio.run(get_activities_tool("museums", "less than 3 hours", None))
