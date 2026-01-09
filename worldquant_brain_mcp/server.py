"""MCP Server for WorldQuant Brain API."""

import os
import json
import logging
from typing import Any, Optional
from datetime import datetime

from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
import mcp.server.stdio
from worldquant import API

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worldquant-brain-mcp")

# Create server instance
app = Server("worldquant-brain-mcp")

# Global API client
_api_client: Optional[API] = None


def get_api_client() -> API:
    """Get or create WorldQuant Brain API client."""
    global _api_client
    if _api_client is None:
        email = os.getenv("WORLDQUANT_EMAIL")
        password = os.getenv("WORLDQUANT_PASSWORD")
        
        if not email or not password:
            raise ValueError(
                "WORLDQUANT_EMAIL and WORLDQUANT_PASSWORD environment variables must be set"
            )
        
        _api_client = API(email=email, password=password)
        logger.info("WorldQuant Brain API client initialized")
    
    return _api_client


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="submit_alpha",
            description="Submit an alpha expression to WorldQuant Brain for simulation. "
                       "Returns simulation results including performance metrics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "alpha_expression": {
                        "type": "string",
                        "description": "The alpha expression to submit (e.g., 'rank(close)')",
                    },
                    "region": {
                        "type": "string",
                        "description": "Region for simulation (e.g., 'USA', 'CHN', 'EUR')",
                        "default": "USA",
                    },
                    "universe": {
                        "type": "string",
                        "description": "Universe for simulation (e.g., 'TOP3000', 'TOP500')",
                        "default": "TOP3000",
                    },
                    "delay": {
                        "type": "integer",
                        "description": "Trading delay in days (0 or 1)",
                        "default": 1,
                    },
                },
                "required": ["alpha_expression"],
            },
        ),
        Tool(
            name="get_alpha",
            description="Get details about a specific alpha by its ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "alpha_id": {
                        "type": "string",
                        "description": "The ID of the alpha to retrieve",
                    },
                },
                "required": ["alpha_id"],
            },
        ),
        Tool(
            name="list_alphas",
            description="List all alphas in your account with their status and performance metrics",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of alphas to return",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="get_simulation",
            description="Get simulation results for a specific alpha",
            inputSchema={
                "type": "object",
                "properties": {
                    "alpha_id": {
                        "type": "string",
                        "description": "The ID of the alpha",
                    },
                },
                "required": ["alpha_id"],
            },
        ),
        Tool(
            name="check_expression",
            description="Check if an alpha expression is valid without submitting it",
            inputSchema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The alpha expression to validate",
                    },
                },
                "required": ["expression"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    try:
        api = get_api_client()
        
        if name == "submit_alpha":
            alpha_expression = arguments["alpha_expression"]
            region = arguments.get("region", "USA")
            universe = arguments.get("universe", "TOP3000")
            delay = arguments.get("delay", 1)
            
            logger.info(f"Submitting alpha: {alpha_expression}")
            
            # Submit alpha and wait for simulation
            alpha = api.submit_alpha(
                alpha=alpha_expression,
                region=region,
                universe=universe,
                delay=delay,
            )
            
            # Get simulation results
            simulation = api.get_simulation(alpha.id)
            
            result = {
                "alpha_id": alpha.id,
                "status": alpha.status,
                "expression": alpha_expression,
                "region": region,
                "universe": universe,
                "delay": delay,
                "simulation": {
                    "sharpe": simulation.sharpe if hasattr(simulation, "sharpe") else None,
                    "returns": simulation.returns if hasattr(simulation, "returns") else None,
                    "turnover": simulation.turnover if hasattr(simulation, "turnover") else None,
                    "fitness": simulation.fitness if hasattr(simulation, "fitness") else None,
                },
                "created_at": datetime.now().isoformat(),
            }
            
            return [
                TextContent(
                    type="text",
                    text=f"Alpha submitted successfully!\n\n{json.dumps(result, indent=2)}",
                )
            ]
        
        elif name == "get_alpha":
            alpha_id = arguments["alpha_id"]
            logger.info(f"Getting alpha: {alpha_id}")
            
            alpha = api.get_alpha(alpha_id)
            
            result = {
                "alpha_id": alpha.id,
                "status": alpha.status,
                "expression": alpha.expression if hasattr(alpha, "expression") else None,
                "created_at": alpha.created_at if hasattr(alpha, "created_at") else None,
            }
            
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )
            ]
        
        elif name == "list_alphas":
            limit = arguments.get("limit", 10)
            logger.info(f"Listing alphas (limit: {limit})")
            
            alphas = api.list_alphas(limit=limit)
            
            result = {
                "count": len(alphas),
                "alphas": [
                    {
                        "alpha_id": alpha.id,
                        "status": alpha.status,
                        "expression": alpha.expression if hasattr(alpha, "expression") else None,
                        "created_at": alpha.created_at if hasattr(alpha, "created_at") else None,
                    }
                    for alpha in alphas
                ],
            }
            
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )
            ]
        
        elif name == "get_simulation":
            alpha_id = arguments["alpha_id"]
            logger.info(f"Getting simulation for alpha: {alpha_id}")
            
            simulation = api.get_simulation(alpha_id)
            
            result = {
                "alpha_id": alpha_id,
                "sharpe": simulation.sharpe if hasattr(simulation, "sharpe") else None,
                "returns": simulation.returns if hasattr(simulation, "returns") else None,
                "turnover": simulation.turnover if hasattr(simulation, "turnover") else None,
                "fitness": simulation.fitness if hasattr(simulation, "fitness") else None,
                "long_count": simulation.long_count if hasattr(simulation, "long_count") else None,
                "short_count": simulation.short_count if hasattr(simulation, "short_count") else None,
            }
            
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )
            ]
        
        elif name == "check_expression":
            expression = arguments["expression"]
            logger.info(f"Checking expression: {expression}")
            
            # Note: The worldquant library may not have a direct check method
            # This would need to be implemented based on the actual API
            result = {
                "expression": expression,
                "status": "Expression syntax check not directly available",
                "suggestion": "Try submitting the alpha to validate it",
            }
            
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )
            ]
        
        else:
            return [
                TextContent(
                    type="text",
                    text=f"Unknown tool: {name}",
                )
            ]
    
    except Exception as e:
        logger.error(f"Error in {name}: {str(e)}")
        return [
            TextContent(
                type="text",
                text=f"Error: {str(e)}",
            )
        ]


async def main():
    """Run the MCP server."""
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
