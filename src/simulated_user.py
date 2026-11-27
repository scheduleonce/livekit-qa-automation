import os
import logging
from openai import AsyncAzureOpenAI

class SimulatedCaller:
    def __init__(self, scenario_data: dict):
        # 1. Initialize Azure OpenAI Client
        self.client = AsyncAzureOpenAI(
            api_key=os.environ.get("AZURE_OPENAI_API_KEY"),  
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT")
        )
        self.deployment_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o") # Your Azure deployment name

        # 2. Extract data from your JSON scenario
        objective = scenario_data.get("objective", "")
        test_data = scenario_data.get("testData", {})
        
        # Format the test details into bullet points
        details_str = "\n".join([f"- {key.capitalize()}: {value}" for key, value in test_data.items()])

        # 3. Build the System Prompt dynamically
        system_prompt = f"""You are a CUSTOMER calling a business on the phone.
You are interacting with an AI Phone Agent. 
In this system, the AI Phone Agent's messages will come in labeled as 'user'. 
You must generate the CUSTOMER's replies. DO NOT act as the receptionist or agent.
        
        YOUR OBJECTIVE:
        {objective}

        YOUR PERSONAL DETAILS:
        {details_str}

        STRICT RULES:
        1. You are the customer. Never confirm the booking yourself.
        2. Keep answers short, conversational, and human-like (try to keep it one sentence).
        3. Do NOT volunteer your personal details (email, phone, name) unless the agent explicitly asks for them.
        4. Wait for the agent to guide the conversation. Only answer the question they just asked.
        5. If the agent confirms the meeting is booked or says goodbye, include the exact tag [END_CALL] in your response.
        6. If the requested time is not available, continue with the earliest time offered by the agent.
        """

        self.conversation_history = [{"role": "system", "content": system_prompt}]

    async def generate_reply(self, agent_message: str) -> str:
        try:
            # Note: Azure uses 'model' kwarg mapped to your deployment_name
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=self.conversation_history,
                temperature=0.7
            )
            
            reply_text = response.choices[0].message.content.strip()
            self.conversation_history.append({"role": "assistant", "content": reply_text})
            return reply_text
            
        except Exception as exc:
            logging.error(f"Azure OpenAI Error: {exc}")
            return "I'm sorry, I didn't catch that."

    async def generate_initial_utterance(self) -> str:
        """Generate a short opening line from the caller to start the conversation."""
        try:
            prompt = self.conversation_history + [
                {
                    "role": "user",
                    "content": "You are a customer on a phone call. Write a single short opening sentence to start the conversation with the agent."
                }
            ]
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=prompt,
                temperature=0.7
            )
            opening_line = response.choices[0].message.content.strip()
            self.conversation_history.append({"role": "assistant", "content": opening_line})
            return opening_line
        except Exception as exc:
            logging.error(f"Azure OpenAI Error while generating initial utterance: {exc}")
            return "Hello, I need help with a question."

    async def close(self):
        """Properly close the AsyncAzureOpenAI client."""
        try:
            await self.client.close()
        except Exception as e:
            logging.error(f"Error closing Azure OpenAI client: {e}")