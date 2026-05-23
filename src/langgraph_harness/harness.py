"""
Harness: integra registry + permission policy + LLM en un StateGraph LangGraph
reutilizable, con wait no-bloqueante.

El wait no-bloqueante usa el interrupt() nativo de LangGraph + threading.Timer:
cuando una ControlTool con interrupts=True corre, el grafo persiste su estado
en el checkpointer y devuelve el control al caller. Un Timer reanuda el grafo
N segundos después con Command(resume=...), sin bloquear ningún hilo.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Annotated, Sequence, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from .permissions import PermissionPolicy
from .registry import ToolRegistry
from .tools.base import ControlTool


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


class Harness:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        *,
        system_prompt: str | None = None,
        model: str = "claude-sonnet-4-6",
        checkpointer=None,
        verbose: bool = True,
    ) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy()
        self.system_prompt = system_prompt
        self.verbose = verbose

        tools = registry.all()
        self.llm = ChatAnthropic(model=model).bind_tools(tools)
        self.graph = self._build_graph(checkpointer or MemorySaver())

    def _build_graph(self, checkpointer):
        graph = StateGraph(AgentState)
        graph.add_node("llm", self._llm_node)
        graph.add_node("tools", self._tools_node)
        graph.add_edge(START, "llm")
        graph.add_conditional_edges("llm", self._should_continue, {"tools": "tools", END: END})
        graph.add_edge("tools", "llm")
        return graph.compile(checkpointer=checkpointer)

    def _llm_node(self, state: AgentState) -> dict:
        return {"messages": [self.llm.invoke(state["messages"])]}

    def _tools_node(self, state: AgentState) -> dict:
        last_message = state["messages"][-1]
        results = []

        for call in last_message.tool_calls:
            tool = self.registry.get(call["name"])
            args = call["args"]
            tool_id = call["id"]

            if not self.policy.check(tool, args):
                results.append(ToolMessage(
                    content=self._deny_message(tool.name),
                    tool_call_id=tool_id,
                ))
                continue

            if isinstance(tool, ControlTool) and tool.interrupts:
                payload = tool.interrupt_payload(**args)
                payload["tool_call_id"] = tool_id
                interrupt(payload)
                # La ejecución continúa aquí solo en la reanudación.
                results.append(ToolMessage(
                    content=tool.resume_message(payload),
                    tool_call_id=tool_id,
                    name=tool.name,
                ))
            else:
                results.append(ToolMessage(
                    content=str(tool.invoke(args)),
                    tool_call_id=tool_id,
                    name=tool.name,
                ))

        return {"messages": results}

    @staticmethod
    def _should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    @staticmethod
    def _deny_message(tool_name: str) -> str:
        return (
            f"ERROR: permiso denegado para '{tool_name}'. "
            "El usuario rechazó esta acción. Probá un enfoque alternativo o explicá por qué la necesitás."
        )

    def _initial_messages(self, thread_id: str, message: str) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        if self.system_prompt and not self._has_history(thread_id):
            messages.append(SystemMessage(content=self.system_prompt))
        messages.append(HumanMessage(content=message))
        return messages

    def _has_history(self, thread_id: str) -> bool:
        state = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return bool(state.values.get("messages")) if state else False

    def run(self, thread_id: str, message: str) -> str | None:
        return self._invoke(thread_id, {"messages": self._initial_messages(thread_id, message)})

    def chat(self, thread_id: str, message: str) -> Iterator[tuple]:
        """
        Turno interactivo: streamea eventos a medida que ocurren y espera
        el `wait` de forma sincrónica (bloquea el turno hasta reanudar).

        Yields tuplas:
          ("assistant", text)
          ("tool_call", name, args)
          ("tool_result", name, content)
          ("wait", seconds, reason)
        """
        config = {"configurable": {"thread_id": thread_id}}
        invocation = {"messages": self._initial_messages(thread_id, message)}

        while True:
            pending_wait = None
            for chunk in self.graph.stream(invocation, config=config, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    payload = chunk["__interrupt__"][0].value
                    if isinstance(payload, dict) and payload.get("type") == "wait":
                        pending_wait = payload
                        yield ("wait", float(payload.get("wait_seconds", 1)), payload.get("reason", ""))
                    continue
                for update in chunk.values():
                    for msg in update.get("messages", []):
                        yield from self._events_for_message(msg)

            if pending_wait:
                time.sleep(float(pending_wait.get("wait_seconds", 1)))
                invocation = Command(resume={"ok": True})
            else:
                break

    @classmethod
    def _events_for_message(cls, msg) -> Iterator[tuple]:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                yield ("tool_call", call["name"], call["args"])
            text = cls._text(msg.content)
            if text.strip():
                yield ("assistant", text)
        elif isinstance(msg, ToolMessage):
            yield ("tool_result", msg.name or "?", str(msg.content))

    @staticmethod
    def _text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return str(content)

    def _invoke(self, thread_id: str, invocation) -> str | None:
        config = {"configurable": {"thread_id": thread_id}}
        result = self.graph.invoke(invocation, config=config)

        interrupts = result.get("__interrupt__", [])
        if interrupts:
            payload = interrupts[0].value
            if isinstance(payload, dict) and payload.get("type") == "wait":
                seconds = float(payload.get("wait_seconds", 1))
                reason = payload.get("reason", "")
                if self.verbose:
                    print(f"\n[{thread_id}] wait {seconds}s: '{reason}' — control devuelto al caller.")

                # daemon=True: el proceso puede salir antes de que dispare el timer;
                # usar non-daemon + join() si la reanudación no puede perderse.
                timer = threading.Timer(seconds, self._resume, args=(thread_id,))
                timer.daemon = True
                timer.start()
                return None

        final = result.get("messages", [])
        if final:
            content = final[-1].content
            if self.verbose:
                print(f"\n[{thread_id}] terminó:\n{content}")
            return content
        return None

    def _resume(self, thread_id: str) -> str | None:
        if self.verbose:
            print(f"\n[{thread_id}] timer disparado — reanudando.")
        return self._invoke(thread_id, Command(resume={"ok": True}))
