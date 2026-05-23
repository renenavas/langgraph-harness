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
from typing import Annotated, NotRequired, Sequence, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
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
    summary: NotRequired[str]


SUMMARY_PROMPT = (
    "Sos un compresor de contexto. Te paso un fragmento de conversación entre un usuario y un "
    "agente de coding (con sus llamadas a tools). Devolvé un resumen acumulativo, conciso pero "
    "completo, que preserve: decisiones tomadas, hechos y datos concretos, rutas de archivos, "
    "estado de tareas en curso y cualquier hilo abierto. No inventes nada que no esté en el "
    "fragmento. Escribí en el mismo idioma que la conversación. Devolvé solo el resumen."
)


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
        summarize: bool = True,
        summary_after_tokens: int = 12_000,
        keep_last_messages: int = 8,
        verbose: bool = True,
    ) -> None:
        self.registry = registry
        self.policy = policy or PermissionPolicy()
        self.system_prompt = system_prompt
        self.wakeup_store = wakeup_store
        self.model = model
        self.summarize = summarize
        self.summary_after_tokens = summary_after_tokens
        self.keep_last_messages = keep_last_messages
        self.verbose = verbose

        tools = registry.all()
        self.llm = ChatAnthropic(model=model).bind_tools(tools)
        self._summarizer = ChatAnthropic(model=model)  # sin tools: solo resume
        self.graph = self._build_graph(checkpointer or MemorySaver())

        # Inyectarse en cada tool que lo necesite (p. ej. Task, que lanza sub-agentes).
        for tool in tools:
            tool.bind_harness(self)

    def _build_graph(self, checkpointer):
        graph = StateGraph(AgentState)
        graph.add_node("summarize", self._summarize_node)
        graph.add_node("llm", self._llm_node)
        graph.add_node("tools", self._tools_node)
        # Pasamos por summarize justo antes de cada llamada al LLM, así el historial
        # queda acotado tanto al arrancar un turno como tras ejecutar tools.
        graph.add_edge(START, "summarize")
        graph.add_edge("summarize", "llm")
        graph.add_conditional_edges("llm", self._should_continue, {"tools": "tools", END: END})
        graph.add_edge("tools", "summarize")
        return graph.compile(checkpointer=checkpointer)

    def _llm_node(self, state: AgentState) -> dict:
        prompt_messages = self._with_summary(list(state["messages"]), state.get("summary", ""))
        return {"messages": [self.llm.invoke(prompt_messages)]}

    def _summarize_node(self, state: AgentState) -> dict:
        if not self.summarize:
            return {}
        messages = list(state["messages"])
        if self._estimate_tokens(messages) < self.summary_after_tokens:
            return {}

        has_system = bool(messages) and isinstance(messages[0], SystemMessage)
        body = messages[1:] if has_system else messages

        cut = self._cut_index(body, self.keep_last_messages)
        to_summarize = body[:cut]
        if not to_summarize:
            return {}

        new_summary = self._make_summary(to_summarize, state.get("summary", ""))
        removals = [RemoveMessage(id=m.id) for m in to_summarize if m.id is not None]
        if self.verbose:
            print(f"\n[summarize] comprimí {len(removals)} mensajes viejos en un resumen.")
        return {"messages": removals, "summary": new_summary}

    def _make_summary(self, messages: list[BaseMessage], existing: str) -> str:
        transcript = self._render_transcript(messages)
        context = f"Resumen previo a integrar:\n{existing}\n\n" if existing else ""
        response = self._summarizer.invoke([
            SystemMessage(content=SUMMARY_PROMPT),
            HumanMessage(content=f"{context}Fragmento a resumir:\n{transcript}"),
        ])
        return self._text(response.content).strip()

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
                    # Un nodo que no cambia el estado (p. ej. summarize en no-op) emite
                    # None como update en stream_mode="updates".
                    if not update:
                        continue
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

    @staticmethod
    def _estimate_tokens(messages: Sequence[BaseMessage]) -> int:
        """Estimación barata (~4 chars/token) para decidir cuándo resumir, sin llamar a la API."""
        chars = sum(
            len(m.content) if isinstance(m.content, str) else len(str(m.content))
            for m in messages
        )
        return chars // 4

    @staticmethod
    def _cut_index(body: Sequence[BaseMessage], keep_last: int) -> int:
        """
        Índice donde arranca la ventana reciente a conservar. Se avanza hasta el próximo
        HumanMessage en/después de (len - keep_last) para que el corte caiga en un borde de
        turno y nunca parta un par tool_call/tool_result (lo que rompería la API de Anthropic).
        Devuelve 0 (no resumir) si no hay un borde seguro en la cola.
        """
        if len(body) <= keep_last:
            return 0
        start = len(body) - keep_last
        for i in range(start, len(body)):
            if isinstance(body[i], HumanMessage):
                return i
        return 0

    @staticmethod
    def _with_summary(messages: list[BaseMessage], summary: str) -> list[BaseMessage]:
        """Pliega el resumen dentro del system prompt (efímero) para la llamada al LLM."""
        if not summary:
            return messages
        block = f"## Resumen de la conversación previa\n{summary}"
        if messages and isinstance(messages[0], SystemMessage):
            merged = SystemMessage(content=f"{messages[0].content}\n\n{block}")
            return [merged, *messages[1:]]
        return [SystemMessage(content=block), *messages]

    @classmethod
    def _render_transcript(cls, messages: Sequence[BaseMessage]) -> str:
        lines = []
        for m in messages:
            if isinstance(m, SystemMessage):
                role = "System"
            elif isinstance(m, HumanMessage):
                role = "User"
            elif isinstance(m, AIMessage):
                role = "Assistant"
            elif isinstance(m, ToolMessage):
                role = f"Tool[{m.name or '?'}]"
            else:
                role = "?"
            text = cls._text(m.content)
            if isinstance(m, AIMessage) and m.tool_calls:
                calls = "; ".join(f"{c['name']}({c['args']})" for c in m.tool_calls)
                text = f"{text} «llama: {calls}»".strip()
            lines.append(f"{role}: {text}")
        return "\n".join(lines)

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
