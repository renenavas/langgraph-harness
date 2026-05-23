"""
Harness: integra registry + permission policy + LLM en un StateGraph LangGraph
reutilizable, con wait no-bloqueante.

El wait no-bloqueante usa el interrupt() nativo de LangGraph: cuando una
ControlTool con interrupts=True corre, el grafo persiste su estado en el
checkpointer y devuelve el control al caller, sin bloquear ningún hilo.

La reanudación se hace de dos maneras:
  - Sin `wakeup_store`: un threading.Timer en-proceso reanuda N segundos después
    (sirve para procesos efímeros / demos; la cita muere si el proceso muere).
  - Con `wakeup_store`: la cita se persiste en SQLite y un worker externo
    (worker.py) la reanuda. Sobrevive a que el proceso se apague — es el
    equivalente single-host de ScheduleWakeup.
"""

from __future__ import annotations

import threading
import time
import uuid
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
from .scheduler import WakeupStore
from .tools.base import ControlTool


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


DEFAULT_SUBAGENT_PROMPT = (
    "Sos un sub-agente autónomo. Resolvé la tarea que te dan de punta a punta y, al terminar, "
    "respondé con el resultado final concreto (lo que el agente padre necesita saber), no con "
    "un relato de los pasos. No tenés acceso a la conversación del padre: trabajá solo con lo "
    "que está en el prompt. Si una tool falla, leé el error y reintentá."
)


class Harness:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        *,
        system_prompt: str | None = None,
        model: str = "claude-sonnet-4-6",
        checkpointer=None,
        wakeup_store: WakeupStore | None = None,
        verbose: bool = True,
    ) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy()
        self.system_prompt = system_prompt
        self.wakeup_store = wakeup_store
        self.model = model
        self.verbose = verbose

        tools = registry.all()
        self.llm = ChatAnthropic(model=model).bind_tools(tools)
        self.graph = self._build_graph(checkpointer or MemorySaver())

        # Inyectarse en cada tool que lo necesite (p. ej. Task, que lanza sub-agentes).
        for tool in tools:
            tool.bind_harness(self)

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

    def spawn_subagent(self, prompt: str) -> str:
        """
        Corre un sub-agente síncrono hasta el final y devuelve su texto final.

        El sub-agente hereda modelo y policy, pero solo recibe las tools "de trabajo"
        (no las ControlTool): así no puede lanzar otros sub-agentes ni agendar wakeups,
        y termina en una sola pasada bloqueante. Estado efímero (MemorySaver propio).
        """
        sub_tools = [t for t in self.registry.all() if not isinstance(t, ControlTool)]
        sub = Harness(
            registry=ToolRegistry(sub_tools),
            policy=self.policy,
            system_prompt=DEFAULT_SUBAGENT_PROMPT,
            model=self.model,
            verbose=False,
        )
        thread_id = f"subagent-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        result = sub.graph.invoke(
            {"messages": sub._initial_messages(thread_id, prompt)}, config=config
        )
        messages = result.get("messages", [])
        return self._text(messages[-1].content) if messages else "(el sub-agente no produjo salida)"

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

                if self.wakeup_store is not None:
                    self.wakeup_store.schedule(thread_id, time.time() + seconds, payload)
                    if self.verbose:
                        print(f"\n[{thread_id}] wait {seconds}s: '{reason}' — cita persistida; el worker reanuda.")
                    return None

                if self.verbose:
                    print(f"\n[{thread_id}] wait {seconds}s: '{reason}' — control devuelto al caller.")
                # daemon=True: el proceso puede salir antes de que dispare el timer;
                # usar non-daemon + join() si la reanudación no puede perderse.
                timer = threading.Timer(seconds, self.resume, args=(thread_id,))
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

    def resume(self, thread_id: str) -> str | None:
        """Reanuda un grafo suspendido en un wait. Lo llama el Timer o el worker."""
        if self.verbose:
            print(f"\n[{thread_id}] reanudando.")
        return self._invoke(thread_id, Command(resume={"ok": True}))
