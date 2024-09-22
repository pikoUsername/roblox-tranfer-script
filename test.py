import os
from dotenv import load_dotenv

import pika



def second_main():
    load_dotenv()

    params = pika.URLParameters(os.environ["queue_dsn"])
    conn = pika.BlockingConnection(params)

    channel = conn.channel()

    for _ in range(1):
        channel.basic_publish(
            exchange="url",
            routing_key="url_queue",
            # body=b'{"errors": [], "status_code": <StatusCodes.already_bought: 401>, "info": "none", "tx_id": 3}',
            body=b'{"url": "https://www.roblox.com/game-pass/3717053/The-Cheapest-Game-Pass", "price": 1, "tx_id": 2}'
        )

    conn.close()


if __name__ == "__main__":
    second_main()
