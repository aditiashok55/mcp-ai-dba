from mcp_server.db import get_connection


def get_database_health() -> dict:
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:

                cursor.execute("SELECT version();")
                version = cursor.fetchone()[0]

                cursor.execute("SELECT current_database();")
                database = cursor.fetchone()[0]

                cursor.execute("SHOW max_connections;")
                max_connections = int(cursor.fetchone()[0])

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_stat_activity;
                    """
                )
                current_connections = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_stat_activity
                    WHERE state = 'active';
                    """
                )
                active_connections = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM pg_stat_activity
                    WHERE state = 'idle';
                    """
                )
                idle_connections = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT now() - pg_postmaster_start_time();
                    """
                )
                uptime = str(cursor.fetchone()[0])

        return {
            "status": "healthy",
            "database": database,
            "version": version,
            "max_connections": max_connections,
            "current_connections": current_connections,
            "active_connections": active_connections,
            "idle_connections": idle_connections,
            "uptime": uptime,
        }

    except Exception as exc:
        return {
            "status": "unhealthy",
            "error": str(exc),
        }