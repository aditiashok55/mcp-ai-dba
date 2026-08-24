from mcp_server.db import get_connection


def check_database_connection() -> dict:
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                result = cursor.fetchone()

        return {
            "status": "healthy",
            "database_reachable": result == (1,),
        }

    except Exception as exc:
        return {
            "status": "unhealthy",
            "database_reachable": False,
            "error": str(exc),
        }