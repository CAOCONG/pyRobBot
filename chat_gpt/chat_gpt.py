#!/usr/bin/env python3
import datetime

import openai
import tiktoken

from . import GeneralConstants
from .chat_context import BaseChatContext, EmbeddingBasedChatContext
from .database import TokenUsageDatabase


class Chat:
    def __init__(
        self, model: str, base_instructions: str, send_full_history: bool = False
    ):
        self.model = model
        self.username = "chat_user"
        self.assistant_name = f"chat_{model.replace('.', '_')}"
        self.system_name = "chat_manager"

        self.ground_ai_instructions = " ".join(
            [
                instruction.strip()
                for instruction in [
                    f"Your name is {self.assistant_name}",
                    f"You are a helpful assistant to {self.username}.",
                    "You answer correctly. You do not lie.",
                    f"{base_instructions.strip(' .')}.",
                    f"You follow all directives by {self.system_name}.",
                ]
                if instruction.strip()
            ]
        )

        self.token_usage = {"input": 0, "output": 0}
        self.token_usage_db = TokenUsageDatabase(
            fpath=GeneralConstants.TOKEN_USAGE_DATABASE,
            model=self.model,
        )

        if send_full_history:
            self.context = BaseChatContext(parent_chat=self)
        else:
            self.context = EmbeddingBasedChatContext(parent_chat=self)

    def start(self):
        conversation = [
            {
                "role": "system",
                "name": self.system_name,
                "content": self.ground_ai_instructions,
            }
        ]
        try:
            while True:
                question = input(f"{self.username}: ").strip()
                if not question:
                    continue

                # Add context to the conversation
                conversation = self.context.add_user_input(
                    conversation=conversation, user_input=question
                )

                # Update number of input tokens
                self.token_usage["input"] += sum(
                    self.get_n_tokens(string=msg["content"]) for msg in conversation
                )

                print(f"{self.assistant_name}: ", end="")
                full_reply_content = ""
                for token in _make_api_call(conversation=conversation, model=self.model):
                    print(token, end="")
                    full_reply_content += token
                print("\n")

                # Update number of output tokens
                self.token_usage["output"] += self.get_n_tokens(full_reply_content)

                # Update context with the reply
                conversation = self.context.add_chat_reply(
                    conversation=conversation, chat_reply=full_reply_content.strip()
                )

        except (KeyboardInterrupt, EOFError):
            print("Exiting chat.")
        finally:
            self.report_token_usage()

    def get_n_tokens(self, string: str) -> int:
        return _num_tokens_from_string(string=string, model=self.model)

    def report_token_usage(self):
        print()
        print("Token usage summary:")
        for k, v in self.token_usage.items():
            print(f"    > {k.capitalize()}: {v}")
        print(f"    > Total:  {sum(self.token_usage.values())}")
        costs = {
            k: v * self.token_usage_db.token_price[k] for k, v in self.token_usage.items()
        }
        print(f"Estimated total cost for this chat: ${sum(costs.values()):.3f}.")

        # Store token usage to database
        self.token_usage_db.create()
        self.token_usage_db.insert_data(
            n_input_tokens=self.token_usage["input"],
            n_output_tokens=self.token_usage["output"],
        )

        accumulated_usage = self.token_usage_db.retrieve_sums()
        accumulated_token_usage = {
            "input": accumulated_usage["n_input_tokens"],
            "output": accumulated_usage["n_output_tokens"],
        }
        acc_costs = {
            "input": accumulated_usage["cost_input_tokens"],
            "output": accumulated_usage["cost_output_tokens"],
        }
        print()
        since = datetime.datetime.fromtimestamp(
            accumulated_usage["earliest_timestamp"], datetime.timezone.utc
        ).isoformat(sep=" ", timespec="seconds")
        print(f"Accumulated token usage since {since.replace('+00:00', 'Z')}:")
        for k, v in accumulated_token_usage.items():
            print(f"    > {k.capitalize()}: {v}")
        print(f"    > Total:  {sum(accumulated_token_usage.values())}")
        print(f"Estimated total costs since same date: ${sum(acc_costs.values()):.3f}.")


def _make_api_call(conversation: list, model: str):
    success = False
    while not success:
        try:
            for line in openai.ChatCompletion.create(
                model=model,
                messages=conversation,
                request_timeout=30,
                stream=True,
                temperature=0.8,
            ):
                reply_content_token = getattr(line.choices[0].delta, "content", "")
                yield reply_content_token
                success = True
        except (
            openai.error.ServiceUnavailableError,
            openai.error.Timeout,
        ) as error:
            print(f"    > {error}. Retrying...")


def _num_tokens_from_string(string: str, model: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(string))
