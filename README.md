# TaskManagerBot

## Description

TaskManagerBot is a Telegram bot designed to simplify task management within Telegram groups. It allows users to create, assign, and track tasks directly within their group chats, eliminating the need to switch between different applications.

## Features

*   **Task Creation:** Create tasks for groups and individual users.
*   **Deadline and Descriptions:** Set deadlines and add detailed descriptions to tasks.
*   **Message Attachment:** Attach Telegram messages (text, voice, images) to tasks for context.
*   **Reply-Based Task Creation:** Turn replied messages into tasks or attach them to existing tasks.
*   **Topic-Based Categorization:** Use topics to further categorize and organize tasks.
*   **Admin Privileges Required:** To add the bot to a group or topic, you must first make the bot an administrator in the group or supergroup.

## Installation

1.  **Prerequisites:**
    *   Python 3.7 or higher
    *   PostgreSQL database
2.  **Install Dependencies:**

    ```bash
    pip install -r requirements.txt
    ```
3.  **Configuration:**

    *   Configure the bot by creating a `.env` file. Use the provided `.env.example` file as a template to set the necessary environment variables (e.g., `BOT_TOKEN`, `DATABASE_URL`).
4.  **Run the Bot:**

    ```bash
    python main.py
    ```

## Usage

1.  **Adding the Bot to a Group or Topic:**

    *   **Make the bot an administrator:** Add the bot to your Telegram group or supergroup and grant it administrator privileges. This is *required* for the bot to function correctly.
    *   **Send `/start` command:** In the group or topic, send the `/start` command. This initializes the bot for that group and registers it in the database.

2.  **Using Commands:**

    The bot supports the following commands:

    *   `/start`: Start the bot and display the help message.
        *   Description: Starts the bot and displays a welcome message with available commands.
        *   Example: `/start`


## Technologies

*   [Aiogram](https://github.com/aiogram/aiogram): Telegram Bot Framework
*   [SQLAlchemy](https://www.sqlalchemy.org/): SQL Toolkit and Object-Relational Mapper
*   [PostgreSQL](https://www.postgresql.org/): Relational Database Management System

## Project Structure

*   `main.py`: Main entry point of the bot.
*   `config.py`: Configuration settings for the bot.
*   `database.py`: Database connection and setup.
*   `models.py`: Database models.
*   `handlers/`: Contains the bot's command handlers.
*   `services/`: Contains business logic and database interaction.

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request with your changes.
