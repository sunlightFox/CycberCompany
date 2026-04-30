from __future__ import annotations

from dataclasses import dataclass

from shell_runtime import ShellRuntime
from trace_service import TraceService

from app.core.config import AppConfig
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.brain_repo import BrainRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.design_alignment_repo import DesignAlignmentRepository
from app.db.repositories.execution_boundary_repo import ExecutionBoundaryRepository
from app.db.repositories.knowledge_repo import KnowledgeRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.memory_repo import MemoryRepository
from app.db.repositories.organization_repo import OrganizationRepository
from app.db.repositories.release_repo import ReleaseRepository
from app.db.repositories.retrieval_repo import RetrievalRepository
from app.db.repositories.scheduled_task_repo import ScheduledTaskRepository
from app.db.repositories.settings_repo import SettingsRepository
from app.db.repositories.shell_repo import ShellRepository
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.task_repo import TaskRepository
from app.db.session import Database
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactStore
from app.services.asset import AssetService
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.bootstrap import BootstrapService
from app.services.brain import BrainService
from app.services.brain_decision import BrainDecisionService
from app.services.capability import CapabilityGraphService
from app.services.chat import ChatService
from app.services.chat_experience import ChatExperienceService
from app.services.design_alignment import (
    PersonaHeartService,
    RuntimeContractService,
    SafetyDecisionService,
    VectorService,
)
from app.services.execution_boundary import ExecutionBoundaryService
from app.services.knowledge import KnowledgeService
from app.services.mcp import MCPService
from app.services.memory import MemoryService
from app.services.model_routing import ModelRoutingService
from app.services.release import ReleaseGateService
from app.services.retrieval import RetrievalDiagnosticsService
from app.services.scheduled_tasks import ScheduledTaskService
from app.services.secrets import SecretStore
from app.services.settings import SettingsService
from app.services.shell_switch import ShellSwitchService
from app.services.skill_plugin import SkillPluginService
from app.services.supervisor import SupervisorService
from app.services.tasks import TaskEngine
from app.services.tools import ToolRuntime


@dataclass
class ServiceRegistry:
    config: AppConfig
    db: Database
    shell_runtime: ShellRuntime
    trace_service: TraceService
    audit_service: AuditEventService
    bootstrap_service: BootstrapService
    chat_service: ChatService
    chat_experience_service: ChatExperienceService
    memory_service: MemoryService
    asset_service: AssetService
    asset_broker_service: AssetBrokerService
    capability_service: CapabilityGraphService
    knowledge_service: KnowledgeService
    task_engine: TaskEngine
    scheduled_task_service: ScheduledTaskService
    tool_runtime: ToolRuntime
    skill_plugin_service: SkillPluginService
    mcp_service: MCPService
    supervisor_service: SupervisorService
    shell_switch_service: ShellSwitchService
    release_gate_service: ReleaseGateService
    runtime_contract_service: RuntimeContractService
    safety_decision_service: SafetyDecisionService
    persona_heart_service: PersonaHeartService
    vector_service: VectorService
    retrieval_service: RetrievalDiagnosticsService
    execution_boundary_service: ExecutionBoundaryService
    settings_service: SettingsService
    approval_service: ApprovalService
    artifact_store: ArtifactStore
    brain_service: BrainService
    brain_decision_service: BrainDecisionService
    model_routing_service: ModelRoutingService
    secret_store: SecretStore
    shells: ShellRepository
    organization: OrganizationRepository
    members: MemberRepository
    chat: ChatRepository
    brains: BrainRepository
    memory: MemoryRepository
    assets: AssetRepository
    knowledge: KnowledgeRepository
    tasks: TaskRepository
    scheduled_tasks: ScheduledTaskRepository
    skill_mcp: SkillMcpRepository
    release: ReleaseRepository
    retrieval: RetrievalRepository
    execution_boundary: ExecutionBoundaryRepository
    design_alignment: DesignAlignmentRepository


def build_registry(config: AppConfig, db: Database, shell_runtime: ShellRuntime) -> ServiceRegistry:
    trace_service = TraceService(db)
    audit_service = AuditEventService(db)
    model_routing_service = ModelRoutingService(db, config.model_routing)
    secret_store = SecretStore(config.storage.data_dir / "secrets")
    brain_repo = BrainRepository(db)
    chat_repo = ChatRepository(db)
    member_repo = MemberRepository(db)
    memory_repo = MemoryRepository(db)
    asset_repo = AssetRepository(db)
    knowledge_repo = KnowledgeRepository(db)
    task_repo = TaskRepository(db)
    scheduled_task_repo = ScheduledTaskRepository(db)
    skill_mcp_repo = SkillMcpRepository(db)
    release_repo = ReleaseRepository(db)
    retrieval_repo = RetrievalRepository(db)
    execution_boundary_repo = ExecutionBoundaryRepository(db)
    settings_repo = SettingsRepository(db)
    design_alignment_repo = DesignAlignmentRepository(db)
    capability_service = CapabilityGraphService(
        repo=asset_repo,
        member_repo=member_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    asset_service = AssetService(
        repo=asset_repo,
        secret_store=secret_store,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    asset_broker_service = AssetBrokerService(
        repo=asset_repo,
        capability=capability_service,
        trace_service=trace_service,
        audit_service=audit_service,
        secret_store=secret_store,
        task_repo=task_repo,
    )
    safety_decision_service = SafetyDecisionService(
        repo=design_alignment_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    vector_service = VectorService(
        repo=design_alignment_repo,
        retrieval_repo=retrieval_repo,
        data_dir=config.storage.data_dir,
        trace_service=trace_service,
        secret_store=secret_store,
    )
    knowledge_service = KnowledgeService(
        repo=knowledge_repo,
        asset_repo=asset_repo,
        capability=capability_service,
        trace_service=trace_service,
        audit_service=audit_service,
        vector_service=vector_service,
        retrieval_repo=retrieval_repo,
    )
    memory_service = MemoryService(
        db=db,
        repo=memory_repo,
        chat_repo=chat_repo,
        member_repo=member_repo,
        trace_service=trace_service,
        audit_service=audit_service,
        vector_service=vector_service,
        retrieval_repo=retrieval_repo,
    )
    chat_experience_service = ChatExperienceService(
        chat_repo=chat_repo,
        trace_service=trace_service,
    )
    artifact_store = ArtifactStore(
        root_dir=config.storage.artifact_dir,
        repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    approval_service = ApprovalService(
        repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    execution_boundary_service = ExecutionBoundaryService(
        repo=execution_boundary_repo,
        trace_service=trace_service,
    )
    tool_runtime = ToolRuntime(
        repo=task_repo,
        artifact_store=artifact_store,
        approval_service=approval_service,
        asset_broker=asset_broker_service,
        knowledge_service=knowledge_service,
        memory_service=memory_service,
        trace_service=trace_service,
        audit_service=audit_service,
        safety_decision_service=safety_decision_service,
        execution_boundary_service=execution_boundary_service,
    )
    task_engine = TaskEngine(
        repo=task_repo,
        member_repo=member_repo,
        tool_runtime=tool_runtime,
        artifact_store=artifact_store,
        memory_service=memory_service,
        trace_service=trace_service,
        audit_service=audit_service,
        brain_repo=brain_repo,
        model_routing_service=model_routing_service,
        secret_store=secret_store,
    )
    skill_plugin_service = SkillPluginService(
        repo=skill_mcp_repo,
        task_repo=task_repo,
        tool_runtime=tool_runtime,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    mcp_service = MCPService(
        repo=skill_mcp_repo,
        task_repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
        mcp_config=config.mcp,
        execution_boundary_service=execution_boundary_service,
    )
    tool_runtime.set_extension_services(
        skill_plugin_service=skill_plugin_service,
        mcp_service=mcp_service,
    )
    task_engine.set_extension_services(
        skill_plugin_service=skill_plugin_service,
        mcp_service=mcp_service,
    )
    supervisor_service = SupervisorService(
        repo=task_repo,
        member_repo=member_repo,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    supervisor_service.set_task_detail_provider(task_engine.detail)

    async def extension_replay(task_id: str):  # type: ignore[no-untyped-def]
        return (
            await skill_plugin_service.replay_skill_runs(task_id),
            await mcp_service.replay_mcp_calls(task_id),
        )

    supervisor_service.set_extension_replay_provider(extension_replay)
    task_engine.set_supervisor_service(supervisor_service)
    organization_repo = OrganizationRepository(db)
    shell_switch_service = ShellSwitchService(
        db=db,
        shell_runtime=shell_runtime,
        organization_repo=organization_repo,
        task_repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    release_gate_service = ReleaseGateService(
        repo=release_repo,
        config=config,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    runtime_contract_service = RuntimeContractService(
        repo=design_alignment_repo,
        data_dir=config.storage.data_dir,
    )
    persona_heart_service = PersonaHeartService(
        repo=design_alignment_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    settings_service = SettingsService(
        repo=settings_repo,
        model_routing_config=config.model_routing,
        safety_config=config.safety,
        mcp_config=config.mcp,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    brain_decision_service = BrainDecisionService(
        chat_repo=chat_repo,
        design_repo=design_alignment_repo,
        skill_mcp_repo=skill_mcp_repo,
        trace_service=trace_service,
    )
    scheduled_task_service = ScheduledTaskService(
        repo=scheduled_task_repo,
        member_repo=member_repo,
        task_engine=task_engine,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    return ServiceRegistry(
        config=config,
        db=db,
        shell_runtime=shell_runtime,
        trace_service=trace_service,
        audit_service=audit_service,
        bootstrap_service=BootstrapService(db, shell_runtime, config.app.default_shell),
        chat_service=ChatService(
            db,
            trace_service,
            audit_service,
            model_routing_service,
            secret_store,
            memory_service,
            asset_broker_service,
            persona_heart_service,
            task_engine,
            chat_experience_service,
            brain_decision_service,
            approval_service,
            scheduled_task_service,
        ),
        chat_experience_service=chat_experience_service,
        memory_service=memory_service,
        asset_service=asset_service,
        asset_broker_service=asset_broker_service,
        capability_service=capability_service,
        knowledge_service=knowledge_service,
        task_engine=task_engine,
        scheduled_task_service=scheduled_task_service,
        tool_runtime=tool_runtime,
        skill_plugin_service=skill_plugin_service,
        mcp_service=mcp_service,
        supervisor_service=supervisor_service,
        shell_switch_service=shell_switch_service,
        release_gate_service=release_gate_service,
        runtime_contract_service=runtime_contract_service,
        safety_decision_service=safety_decision_service,
        persona_heart_service=persona_heart_service,
        vector_service=vector_service,
        retrieval_service=RetrievalDiagnosticsService(repo=retrieval_repo),
        execution_boundary_service=execution_boundary_service,
        settings_service=settings_service,
        approval_service=approval_service,
        artifact_store=artifact_store,
        brain_service=BrainService(brain_repo, secret_store, audit_service),
        brain_decision_service=brain_decision_service,
        model_routing_service=model_routing_service,
        secret_store=secret_store,
        shells=ShellRepository(db),
        organization=organization_repo,
        members=member_repo,
        chat=chat_repo,
        brains=brain_repo,
        memory=memory_repo,
        assets=asset_repo,
        knowledge=knowledge_repo,
        tasks=task_repo,
        scheduled_tasks=scheduled_task_repo,
        skill_mcp=skill_mcp_repo,
        release=release_repo,
        retrieval=retrieval_repo,
        execution_boundary=execution_boundary_repo,
        design_alignment=design_alignment_repo,
    )
