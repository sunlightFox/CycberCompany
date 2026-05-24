from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chat_runtime import ChatRuntime
from shell_runtime import ShellRuntime
from trace_service import TraceService

from app.core.config import AppConfig
from app.core.errors import AppError
from core_types import ErrorCode
from app.db.repositories.agent_workbench_repo import AgentWorkbenchRepository
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.brain_repo import BrainRepository
from app.db.repositories.browser_repo import BrowserRepository
from app.db.repositories.browser_workflow_repo import BrowserWorkflowRepository
from app.db.repositories.channel_repo import ChannelRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.checkpoint_repo import CheckpointRepository
from app.db.repositories.design_alignment_repo import DesignAlignmentRepository
from app.db.repositories.execution_boundary_repo import ExecutionBoundaryRepository
from app.db.repositories.external_platform_adapter_repo import (
    ExternalPlatformAdapterRepository,
)
from app.db.repositories.external_platform_repo import ExternalPlatformRepository
from app.db.repositories.goal_repo import GoalRepository
from app.db.repositories.knowledge_repo import KnowledgeRepository
from app.db.repositories.media_repo import MediaRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.memory_repo import MemoryRepository
from app.db.repositories.notification_repo import NotificationRepository
from app.db.repositories.organization_repo import OrganizationRepository
from app.db.repositories.project_deployment_repo import ProjectDeploymentRepository
from app.db.repositories.release_repo import ReleaseRepository
from app.db.repositories.retrieval_repo import RetrievalRepository
from app.db.repositories.scheduled_task_repo import ScheduledTaskRepository
from app.db.repositories.settings_repo import SettingsRepository
from app.db.repositories.shell_repo import ShellRepository
from app.db.repositories.skill_governance_repo import SkillGovernanceRepository
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.skill_repository_repo import SkillRepositoryRepository
from app.db.repositories.task_repo import TaskRepository
from app.db.repositories.voice_repo import VoiceRepository
from app.db.session import Database
from app.channels import register_bundled_channel_extensions
from app.external_platforms import register_bundled_external_platform_extensions
from app.services.agent_workbench import AgentWorkbenchService
from app.services.agent_runtime import AgentRuntime
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactStore
from app.services.asset import AssetService
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.background_workers import BackgroundWorkerService
from app.services.bootstrap import BootstrapService
from app.services.brain import BrainService
from app.services.brain_decision import BrainDecisionService
from app.services.browser_sessions import BrowserSessionService
from app.services.browser_workflows import AutonomousBrowserWorkflowService
from app.services.browser_workflow_runtime import BrowserWorkflowRuntime
from app.services.browser_intent_resolver import BrowserIntentResolver
from app.services.browser_page_state import BrowserPageStateRuntime
from app.services.browser_plan_runtime import BrowserPlanRuntime
from app.services.browser_replay_store import BrowserReplayStore
from app.services.browser_session_runtime import BrowserSessionRuntime
from app.services.capability import CapabilityGraphService
from app.services.channel_connectors import ChannelConnectorRegistry
from app.services.channel_extensions import (
    ChannelExtensionRegistry,
    ChannelGatewayProtocol,
    ChannelGatewayRegistry,
    ChannelRuntimeContext,
)
from app.services.channel_approval_bridge import ChannelApprovalBridge
from app.services.channel_session_context import ChannelSessionContext
from app.services.channel_session_semantics import ChannelSessionSemanticsRuntime
from app.services.channel_stream_bridge import ChannelStreamBridge
from app.services.channels import ChannelBindingService
from app.services.chat import ChatService
from app.services.chat_experience import ChatExperienceService
from app.services.chat_hook_runtime import ChatHookRuntime
from app.services.chat_mainline_readiness import ChatMainlineReadinessService
from app.services.chat_run_ledger import ChatRunLedgerService
from app.services.channel_ingress_runtime import ChannelIngressRuntime
from app.services.conversation_understanding_runtime import ConversationUnderstandingRuntimeService
from app.services.presence_state import PresenceStateResolverService
from app.services.session_context import SessionContextCuratorService
from app.services.response_policy import ResponsePolicyService
from app.services.action_dialogue_mapper import ActionDialogueMapperService
from app.services.silent_continuity import SilentContinuityService
from app.services.chat_quality_shadow import ChatQualityShadowService
from app.services.checkpoints import CheckpointService
from app.services.design_alignment import (
    PersonaHeartService,
    RuntimeContractService,
    SafetyDecisionService,
    VectorService,
)
from app.services.extensions import ExtensionService
from app.services.execution_boundary import ExecutionBoundaryService
from app.services.external_platform_actions import ExternalPlatformActionService
from app.services.external_platform_adapters import ExternalPlatformAdapterService
from app.services.external_platform_extensions import (
    ExternalPlatformExtensionRegistry,
    ExternalPlatformRuntimeContext,
)
from app.services.failure_experience import FailureExperienceService
from app.services.heart_runtime import HeartRuntimeService
from app.services.goals import GoalService
from app.services.knowledge import KnowledgeService
from app.services.mcp import MCPService
from app.services.media import MediaService
from app.services.memory import MemoryService
from app.services.model_routing import ModelRoutingService
from app.services.multimodal_understanding import MultimodalUnderstandingService
from app.services.notifications import NotificationGatewayService
from app.services.office_tools import OfficeToolService
from app.services.persona_runtime import PersonaRuntimeService
from app.services.plane_registry import (
    CapabilityPlaneRegistry,
    ControlPlaneRegistry,
    PolicyPlaneRegistry,
    RuntimePlaneRegistry,
)
from app.schemas.media import VideoWorkflowCreateRequest, VideoWorkflowExecuteRequest
from app.services.project_deployments import (
    HostInstallService,
    ProjectDeploymentService,
    ProjectWorkspaceService,
    ToolchainService,
)
from app.services.release import ReleaseGateService
from app.services.release_gate_runtime import ReleaseGateRuntime
from app.services.release_report_builder import ReleaseReportBuilder
from app.services.retrieval import RetrievalDiagnosticsService
from app.services.response_quality_runtime import ResponseQualityRuntimeService
from app.services.scheduled_tasks import ScheduledTaskService
from app.services.secrets import SecretStore
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.settings import SettingsService
from app.services.shell_switch import ShellSwitchService
from app.services.skill_governance import SkillGovernanceService
from app.services.skill_candidate_extractor import SkillCandidateExtractor
from app.services.skill_plugin import SkillPluginService
from app.services.skill_promotion_runtime import SkillPromotionRuntime
from app.services.skill_repositories import SkillRepositoryService
from app.services.skill_source_resolver import SkillSourceResolver
from app.services.supervisor import SupervisorService
from app.services.session_runtime import SessionRuntime
from app.services.tasks import TaskEngine
from app.services.tone_policy_runtime import TonePolicyRuntimeService
from app.services.tools import ToolRuntime
from app.services.voice import VoiceService
from app.services.video_workflows import VideoWorkflowService


@dataclass
class ServiceRegistry:
    config: AppConfig
    db: Database
    shell_runtime: ShellRuntime
    trace_service: TraceService
    audit_service: AuditEventService
    bootstrap_service: BootstrapService
    chat_service: ChatService
    chat_runtime: ChatRuntime
    agent_runtime: AgentRuntime
    session_runtime: SessionRuntime
    channel_ingress_runtime: ChannelIngressRuntime
    channel_session_semantics_runtime: ChannelSessionSemanticsRuntime
    chat_experience_service: ChatExperienceService
    chat_run_ledger_service: ChatRunLedgerService
    failure_experience_service: FailureExperienceService
    chat_hook_runtime: ChatHookRuntime
    agent_workbench_service: AgentWorkbenchService
    memory_service: MemoryService
    media_service: MediaService
    video_workflow_service: VideoWorkflowService
    asset_service: AssetService
    asset_broker_service: AssetBrokerService
    capability_service: CapabilityGraphService
    knowledge_service: KnowledgeService
    task_engine: TaskEngine
    goal_service: GoalService
    background_worker_service: BackgroundWorkerService
    scheduled_task_service: ScheduledTaskService
    checkpoint_service: CheckpointService
    notification_gateway_service: NotificationGatewayService
    channel_binding_service: ChannelBindingService
    channel_extension_registry: ChannelExtensionRegistry
    channel_gateway_registry: ChannelGatewayRegistry
    wechat_gateway_service: ChannelGatewayProtocol
    feishu_gateway_service: ChannelGatewayProtocol
    browser_session_service: BrowserSessionService
    autonomous_browser_workflow_service: BrowserWorkflowRuntime
    browser_workflow_runtime: BrowserWorkflowRuntime
    browser_session_runtime: BrowserSessionRuntime
    browser_page_state_runtime: BrowserPageStateRuntime
    browser_replay_store: BrowserReplayStore
    project_workspace_service: ProjectWorkspaceService
    project_deployment_service: ProjectDeploymentService
    toolchain_service: ToolchainService
    host_install_service: HostInstallService
    external_platform_action_service: ExternalPlatformActionService
    external_platform_adapter_service: ExternalPlatformAdapterService
    external_platform_extension_registry: ExternalPlatformExtensionRegistry
    tool_runtime: ToolRuntime
    skill_governance_service: SkillGovernanceService
    skill_plugin_service: SkillPluginService
    extension_service: ExtensionService
    skill_repository_service: SkillRepositoryService
    mcp_service: MCPService
    supervisor_service: SupervisorService
    shell_switch_service: ShellSwitchService
    release_gate_service: ReleaseGateService
    chat_mainline_readiness_service: ChatMainlineReadinessService
    release_gate_runtime: ReleaseGateRuntime
    release_report_builder: ReleaseReportBuilder
    runtime_contract_service: RuntimeContractService
    safety_policy_service: RuntimeSafetyPolicyService
    safety_decision_service: SafetyDecisionService
    persona_heart_service: PersonaHeartService
    vector_service: VectorService
    retrieval_service: RetrievalDiagnosticsService
    execution_boundary_service: ExecutionBoundaryService
    settings_service: SettingsService
    voice_service: VoiceService
    approval_service: ApprovalService
    skill_candidate_extractor: SkillCandidateExtractor
    skill_promotion_runtime: SkillPromotionRuntime
    artifact_store: ArtifactStore
    brain_service: BrainService
    brain_decision_service: BrainDecisionService
    model_routing_service: ModelRoutingService
    secret_store: SecretStore
    control_plane_registry: ControlPlaneRegistry
    runtime_registry: RuntimePlaneRegistry
    capability_registry: CapabilityPlaneRegistry
    policy_registry: PolicyPlaneRegistry
    shells: ShellRepository
    organization: OrganizationRepository
    members: MemberRepository
    chat: ChatRepository
    agent_workbench: AgentWorkbenchRepository
    brains: BrainRepository
    memory: MemoryRepository
    media: MediaRepository
    assets: AssetRepository
    knowledge: KnowledgeRepository
    tasks: TaskRepository
    goals: GoalRepository
    voices: VoiceRepository
    scheduled_tasks: ScheduledTaskRepository
    checkpoints: CheckpointRepository
    notifications: NotificationRepository
    channels: ChannelRepository
    browser: BrowserRepository
    browser_workflows: BrowserWorkflowRepository
    project_deployments: ProjectDeploymentRepository
    external_platform: ExternalPlatformRepository
    external_platform_adapters: ExternalPlatformAdapterRepository
    skill_governance: SkillGovernanceRepository
    skill_mcp: SkillMcpRepository
    skill_repositories: SkillRepositoryRepository
    release: ReleaseRepository
    retrieval: RetrievalRepository
    execution_boundary: ExecutionBoundaryRepository
    design_alignment: DesignAlignmentRepository

    def channel_gateway(self, provider: str) -> ChannelGatewayProtocol:
        return self.channel_gateway_registry.require(provider)


def build_registry(config: AppConfig, db: Database, shell_runtime: ShellRuntime) -> ServiceRegistry:
    trace_service = TraceService(db)
    audit_service = AuditEventService(db)
    model_routing_service = ModelRoutingService(db, config.model_routing)
    secret_store = SecretStore(config.storage.data_dir / "secrets")
    brain_repo = BrainRepository(db)
    chat_repo = ChatRepository(db)
    member_repo = MemberRepository(db)
    agent_workbench_repo = AgentWorkbenchRepository(db)
    memory_repo = MemoryRepository(db)
    media_repo = MediaRepository(db)
    asset_repo = AssetRepository(db)
    browser_repo = BrowserRepository(db)
    browser_workflow_repo = BrowserWorkflowRepository(db)
    project_deployment_repo = ProjectDeploymentRepository(db)
    external_platform_repo = ExternalPlatformRepository(db)
    external_platform_adapter_repo = ExternalPlatformAdapterRepository(db)
    knowledge_repo = KnowledgeRepository(db)
    task_repo = TaskRepository(db)
    goal_repo = GoalRepository(db)
    scheduled_task_repo = ScheduledTaskRepository(db)
    checkpoint_repo = CheckpointRepository(db)
    notification_repo = NotificationRepository(db)
    channel_repo = ChannelRepository(db)
    skill_governance_repo = SkillGovernanceRepository(db)
    skill_mcp_repo = SkillMcpRepository(db)
    skill_repository_repo = SkillRepositoryRepository(db)
    release_repo = ReleaseRepository(db)
    retrieval_repo = RetrievalRepository(db)
    execution_boundary_repo = ExecutionBoundaryRepository(db)
    settings_repo = SettingsRepository(db)
    design_alignment_repo = DesignAlignmentRepository(db)
    voice_repo = VoiceRepository(db)
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
    safety_policy_service = RuntimeSafetyPolicyService(
        settings_repo=settings_repo,
        safety_config=config.safety,
    )
    safety_decision_service = SafetyDecisionService(
        repo=design_alignment_repo,
        trace_service=trace_service,
        audit_service=audit_service,
        safety_policy_service=safety_policy_service,
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
    chat_run_ledger_service = ChatRunLedgerService(
        chat_repo=chat_repo,
        memory_repo=memory_repo,
    )
    chat_hook_runtime = ChatHookRuntime(
        trace_service=trace_service,
        audit_service=audit_service,
        chat_run_ledger_service=chat_run_ledger_service,
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
        chat_run_ledger=chat_run_ledger_service,
        chat_hook_runtime=chat_hook_runtime,
    )
    failure_experience_service = FailureExperienceService(
        repo=memory_repo,
        member_repo=member_repo,
        audit_service=audit_service,
        memory_service=memory_service,
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
    media_service = MediaService(
        repo=media_repo,
        task_repo=task_repo,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
        secret_store=secret_store,
    )
    voice_service = VoiceService(
        repo=voice_repo,
        chat_repo=chat_repo,
        member_repo=member_repo,
        trace_service=trace_service,
        audit_service=audit_service,
        secret_store=secret_store,
        data_dir=config.storage.data_dir,
    )
    multimodal_understanding_service = MultimodalUnderstandingService(
        channel_repo=channel_repo,
        memory_service=memory_service,
        media_service=media_service,
        data_dir=config.storage.data_dir,
        trace_service=trace_service,
    )
    approval_service = ApprovalService(
        repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    checkpoint_service = CheckpointService(
        repo=checkpoint_repo,
        task_repo=task_repo,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    execution_boundary_service = ExecutionBoundaryService(
        repo=execution_boundary_repo,
        trace_service=trace_service,
        safety_policy_service=safety_policy_service,
    )
    browser_session_service = BrowserSessionService(
        repo=browser_repo,
        asset_repo=asset_repo,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    office_tool_service = OfficeToolService(
        artifact_store,
        brain_repo=brain_repo,
        model_routing_service=model_routing_service,
        secret_store=secret_store,
        trace_service=trace_service,
        audit_service=audit_service,
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
        safety_policy_service=safety_policy_service,
        execution_boundary_service=execution_boundary_service,
        browser_session_service=browser_session_service,
        checkpoint_service=checkpoint_service,
        media_service=media_service,
        office_tool_service=office_tool_service,
        chat_hook_runtime=chat_hook_runtime,
    )
    video_workflow_service = VideoWorkflowService(
        repo=media_repo,
        media_service=media_service,
        tool_runtime=tool_runtime,
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
    skill_governance_service = SkillGovernanceService(
        repo=skill_governance_repo,
        skill_repo=skill_mcp_repo,
        task_repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
        capability_service=capability_service,
        safety_policy_service=safety_policy_service,
    )
    skill_repository_service = SkillRepositoryService(
        repo=skill_repository_repo,
        config=config.skills,
        root_dir=config.paths.root_dir,
        trace_service=trace_service,
        audit_service=audit_service,
        skill_repo=skill_mcp_repo,
    )
    skill_source_resolver = SkillSourceResolver(
        root_dir=config.paths.root_dir,
        cache_dir=config.storage.data_dir / "skill-source-cache",
        repository_service=skill_repository_service,
    )
    skill_governance_service.set_source_resolver(skill_source_resolver)
    skill_plugin_service = SkillPluginService(
        repo=skill_mcp_repo,
        task_repo=task_repo,
        tool_runtime=tool_runtime,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
        governance_service=skill_governance_service,
        repository_service=skill_repository_service,
        source_resolver=skill_source_resolver,
        safety_policy_service=safety_policy_service,
    )
    skill_plugin_service.set_repository_service(skill_repository_service)
    extension_service = ExtensionService(
        repo=skill_mcp_repo,
        task_repo=task_repo,
        source_resolver=skill_source_resolver,
        skill_plugin_service=skill_plugin_service,
    )
    agent_workbench_service = AgentWorkbenchService(
        repo=agent_workbench_repo,
        chat_repo=chat_repo,
        member_repo=member_repo,
        memory_repo=memory_repo,
        memory_service=memory_service,
        artifact_root=config.storage.artifact_dir,
        trace_service=trace_service,
        audit_service=audit_service,
        skill_plugin_service=skill_plugin_service,
        skill_repository_service=skill_repository_service,
    )
    mcp_service = MCPService(
        repo=skill_mcp_repo,
        task_repo=task_repo,
        trace_service=trace_service,
        audit_service=audit_service,
        mcp_config=config.mcp,
        execution_boundary_service=execution_boundary_service,
        chat_repo=chat_repo,
        approval_service=approval_service,
    )
    tool_runtime.set_extension_services(
        skill_plugin_service=skill_plugin_service,
        mcp_service=mcp_service,
    )
    skill_plugin_service.set_mcp_service(mcp_service)
    task_engine.set_extension_services(
        skill_plugin_service=skill_plugin_service,
        skill_governance_service=skill_governance_service,
        skill_repository_service=skill_repository_service,
        mcp_service=mcp_service,
    )
    extension_service.set_task_engine(task_engine)
    task_engine.set_browser_evidence_provider(browser_session_service.list_task_evidence)
    task_engine.set_checkpoint_replay_provider(checkpoint_service.replay_checkpoint_data)
    async def media_replay_provider(task_id: str) -> list[dict[str, Any]]:
        media_replay = await media_service.replay_task_media(task_id)
        video_replay = await video_workflow_service.replay_task_video_workflows(task_id)
        if not video_replay:
            return media_replay
        by_media = {str(item.get("media", {}).get("media_id")): item for item in media_replay}
        for workflow in video_replay:
            media_id = str(workflow.get("workflow", {}).get("media_id") or "")
            if media_id in by_media:
                by_media[media_id].setdefault("video_workflows", []).append(workflow)
            else:
                media_replay.append({"media": {"media_id": media_id}, "video_workflows": [workflow]})
        return media_replay

    task_engine.set_media_replay_provider(media_replay_provider)
    async def execute_video_workflow_step(
        task: dict[str, Any],
        step: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        step_input = dict(step.get("input") or {})
        planner_context = dict(step_input.get("planner_context") or task.get("planner_context") or {})
        workflow_profile = dict(step_input.get("workflow_profile") or planner_context.get("video_workflow_profile") or {})
        workflow_id = str(
            step_input.get("workflow_id")
            or (step.get("output") or {}).get("workflow_id")
            or ""
        ).strip()
        workflows = await media_repo.list_video_workflows_by_task(task["task_id"])
        if not workflow_id and workflows:
            workflow_id = str(workflows[-1]["workflow_id"])
        if workflow_id:
            workflow = await video_workflow_service.resume(
                workflow_id,
                step.get("approval_id"),
                trace_id=trace_id,
            )
        else:
            assets = await media_repo.list_assets_by_task(task["task_id"])
            video_assets = [item for item in assets if str(item.get("media_type") or "") == "video"]
            if not video_assets:
                raise AppError(
                    ErrorCode.MEDIA_ASSET_NOT_FOUND,
                    "Video workflow task requires a task-bound video artifact before execution",
                    status_code=409,
                )
            created = await video_workflow_service.create(
                VideoWorkflowCreateRequest(
                    task_id=task["task_id"],
                    media_id=video_assets[-1]["media_id"],
                    goal=str(step_input.get("goal") or task["goal"]),
                    workflow_profile=workflow_profile,
                ),
                trace_id=trace_id,
            )
            workflow = await video_workflow_service.execute(
                created.workflow.workflow_id,
                VideoWorkflowExecuteRequest(approval_id=step.get("approval_id")),
                trace_id=trace_id,
            )
        return {
            "status": workflow.workflow.status,
            "workflow_id": workflow.workflow.workflow_id,
            "approval_id": workflow.workflow.approval_id,
            "workflow": workflow.workflow.model_dump(mode="json"),
            "steps": [item.model_dump(mode="json") for item in workflow.steps],
            "media": workflow.media.model_dump(mode="json") if workflow.media is not None else None,
            "next_step": workflow.next_step,
            "message": workflow.message,
        }

    task_engine.set_video_workflow_executor(execute_video_workflow_step)
    notification_gateway_service = NotificationGatewayService(
        repo=notification_repo,
        asset_service=asset_service,
        asset_broker=asset_broker_service,
        capability=capability_service,
        approval_service=approval_service,
        trace_service=trace_service,
        audit_service=audit_service,
        task_engine=task_engine,
    )
    mcp_service.set_conversation_bridge_services(
        approval_service=approval_service,
        notification_gateway=notification_gateway_service,
    )
    channel_extension_registry = ChannelExtensionRegistry()
    register_bundled_channel_extensions(channel_extension_registry)
    for provider in config.channels.providers:
        channel_extension_registry.require_provider(provider)
    provider_configs = channel_extension_registry.normalized_provider_configs(
        config.channels.providers,
        config.storage.data_dir,
    )
    channel_connector_registry = ChannelConnectorRegistry(
        channel_extension_registry.build_connectors(
            provider_configs,
            config.storage.data_dir,
        )
    )
    channel_binding_service = ChannelBindingService(
        repo=channel_repo,
        asset_repo=asset_repo,
        asset_service=asset_service,
        capability=capability_service,
        notifications=notification_gateway_service,
        connectors=channel_connector_registry,
        artifact_store=artifact_store,
        secret_store=secret_store,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    for provider, runtime in channel_extension_registry.build_notification_providers(
        channel_binding_service,
        voice_service,
    ).items():
        notification_gateway_service.register_provider(provider, runtime)
    approval_service.set_notification_callback(
        notification_gateway_service.notify_approval_required
    )

    async def _handle_approval_resolution(approval: Any, trace_id: str | None) -> None:
        await task_engine.handle_approval_resolved(
            approval.approval_id,
            trace_id=trace_id,
        )
        await external_platform_action_service.continue_after_approval(
            approval,
            adapter_service=external_platform_adapter_service,
            trace_id=trace_id,
        )

    approval_service.set_resolution_callback(_handle_approval_resolution)
    checkpoint_service.set_rollback_notification_callback(
        notification_gateway_service.notify_checkpoint_rollback
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
    release_gate_runtime = ReleaseGateRuntime()
    release_report_builder = ReleaseReportBuilder()
    release_gate_runtime.bind_service(release_gate_service)
    runtime_contract_service = RuntimeContractService(
        repo=design_alignment_repo,
        data_dir=config.storage.data_dir,
    )
    persona_heart_service = PersonaHeartService(
        repo=design_alignment_repo,
        member_repo=member_repo,
        data_dir=config.storage.data_dir,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    persona_runtime = PersonaRuntimeService(persona_heart_service=persona_heart_service)
    heart_runtime = HeartRuntimeService(persona_heart_service=persona_heart_service)
    tone_policy_runtime = TonePolicyRuntimeService(persona_heart_service=persona_heart_service)
    response_quality_runtime = ResponseQualityRuntimeService(
        persona_heart_service=persona_heart_service
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
        failure_experience_service=failure_experience_service,
    )
    chat_quality_shadow_service = ChatQualityShadowService()
    conversation_understanding_runtime_service = ConversationUnderstandingRuntimeService()
    presence_state_service = PresenceStateResolverService()
    session_context_service = SessionContextCuratorService()
    response_policy_service = ResponsePolicyService()
    action_dialogue_mapper_service = ActionDialogueMapperService()
    silent_continuity_service = SilentContinuityService(chat_repo=chat_repo)
    scheduled_task_service = ScheduledTaskService(
        repo=scheduled_task_repo,
        member_repo=member_repo,
        task_engine=task_engine,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    scheduled_task_service.set_notification_callback(
        notification_gateway_service.notify_scheduled_run
    )
    goal_service = GoalService(
        repo=goal_repo,
        member_repo=member_repo,
        scheduled_task_service=scheduled_task_service,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    scheduled_task_service.set_goal_checkin_callback(goal_service.handle_scheduled_checkin)
    background_worker_service = BackgroundWorkerService(
        scheduled_tasks=scheduled_task_service,
        notifications=notification_gateway_service,
        checkpoints=checkpoint_service,
        task_engine=task_engine,
        memory_service=memory_service,
        agent_workbench_service=agent_workbench_service,
        trace_service=trace_service,
        audit_service=audit_service,
        enabled=config.workers.enabled,
        interval_seconds=config.workers.interval_seconds,
        timeout_seconds=config.workers.timeout_seconds,
    )
    project_workspace_service = ProjectWorkspaceService(
        repo=project_deployment_repo,
        member_repo=member_repo,
        data_dir=config.storage.data_dir,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    toolchain_service = ToolchainService(
        repo=project_deployment_repo,
        data_dir=config.storage.data_dir,
    )
    project_deployment_service = ProjectDeploymentService(
        repo=project_deployment_repo,
        workspace_service=project_workspace_service,
        toolchain_service=toolchain_service,
        task_engine=task_engine,
        task_repo=task_repo,
        approval_service=approval_service,
        artifact_store=artifact_store,
        data_dir=config.storage.data_dir,
        trace_service=trace_service,
        audit_service=audit_service,
        safety_policy_service=safety_policy_service,
    )
    host_install_service = HostInstallService(
        repo=project_deployment_repo,
        task_engine=task_engine,
        task_repo=task_repo,
        approval_service=approval_service,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
        brain_repo=brain_repo,
        model_routing_service=model_routing_service,
        secret_store=secret_store,
        safety_policy_service=safety_policy_service,
    )
    external_platform_extension_registry = ExternalPlatformExtensionRegistry()
    register_bundled_external_platform_extensions(external_platform_extension_registry)
    external_platform_runtime_context = ExternalPlatformRuntimeContext(
        external_platform_repo=external_platform_repo,
        external_platform_adapter_repo=external_platform_adapter_repo,
        asset_repo=asset_repo,
        asset_service=asset_service,
        asset_broker=asset_broker_service,
        capability_service=capability_service,
        browser_session_service=browser_session_service,
        artifact_store=artifact_store,
        task_engine=task_engine,
        approval_service=approval_service,
        tool_runtime=tool_runtime,
        trace_service=trace_service,
        audit_service=audit_service,
        safety_policy_service=safety_policy_service,
        skill_plugin_service=skill_plugin_service,
        skill_governance_service=skill_governance_service,
        skill_repository_service=skill_repository_service,
    )
    external_platform_action_service = ExternalPlatformActionService(
        repo=external_platform_repo,
        adapter_repo=external_platform_adapter_repo,
        asset_repo=asset_repo,
        asset_service=asset_service,
        asset_broker=asset_broker_service,
        capability_service=capability_service,
        browser_session_service=browser_session_service,
        artifact_store=artifact_store,
        task_engine=task_engine,
        approval_service=approval_service,
        trace_service=trace_service,
        audit_service=audit_service,
        extension_registry=external_platform_extension_registry,
        runtime_context=external_platform_runtime_context,
        safety_policy_service=safety_policy_service,
        skill_plugin_service=skill_plugin_service,
        skill_governance_service=skill_governance_service,
        skill_repository_service=skill_repository_service,
    )
    external_platform_adapter_service = ExternalPlatformAdapterService(
        repo=external_platform_adapter_repo,
        platform_repo=external_platform_repo,
        tool_runtime=tool_runtime,
        approval_service=approval_service,
        audit_service=audit_service,
        asset_broker=asset_broker_service,
        browser_session_service=browser_session_service,
        extension_registry=external_platform_extension_registry,
        runtime_context=external_platform_runtime_context,
    )
    autonomous_browser_workflow_legacy_service = AutonomousBrowserWorkflowService(
        repo=browser_workflow_repo,
        task_repo=task_repo,
        task_engine=task_engine,
        tool_runtime=tool_runtime,
        approval_service=approval_service,
        audit_service=audit_service,
        safety_policy_service=safety_policy_service,
    )
    browser_page_state_runtime = BrowserPageStateRuntime()
    browser_replay_store = BrowserReplayStore(
        browser_sessions=browser_session_service,
        workflow_repo=browser_workflow_repo,
    )
    browser_session_runtime = BrowserSessionRuntime(
        browser_sessions=browser_session_service,
        asset_broker=asset_broker_service,
        replay_store=browser_replay_store,
    )
    browser_workflow_runtime = BrowserWorkflowRuntime(
        legacy_service=autonomous_browser_workflow_legacy_service,
        intent_resolver=BrowserIntentResolver(repo=browser_workflow_repo),
        plan_runtime=BrowserPlanRuntime(
            repo=browser_workflow_repo,
            task_engine=task_engine,
            task_repo=task_repo,
            response_builder=autonomous_browser_workflow_legacy_service._response,
        ),
        replay_store=browser_replay_store,
    )
    chat_service = ChatService(
        db,
        trace_service,
        audit_service,
        model_routing_service,
        secret_store,
        memory_service,
        agent_workbench_service,
        asset_broker_service,
        persona_heart_service,
        task_engine,
        chat_experience_service,
        brain_decision_service,
        approval_service,
        scheduled_task_service,
        goal_service,
        project_deployment_service,
        host_install_service,
        external_platform_action_service,
        external_platform_adapter_service,
        skill_plugin_service,
        skill_governance_service,
        tool_runtime=tool_runtime,
        voice_service=voice_service,
        safety_policy_service=safety_policy_service,
        chat_quality_shadow_service=chat_quality_shadow_service,
        conversation_understanding_service=conversation_understanding_runtime_service,
        presence_state_service=presence_state_service,
        session_context_service=session_context_service,
        response_policy_service=response_policy_service,
        action_dialogue_mapper_service=action_dialogue_mapper_service,
        silent_continuity_service=silent_continuity_service,
        chat_run_ledger_service=chat_run_ledger_service,
        failure_experience_service=failure_experience_service,
        chat_hook_runtime=chat_hook_runtime,
    )
    chat_runtime = chat_service._runtime_impl
    agent_runtime = AgentRuntime(chat_runtime=chat_runtime)
    chat_service._agent_runtime = agent_runtime
    chat_runtime._bind_context(chat_service)
    chat_runtime._agent_runtime = agent_runtime
    chat_service._execution._runner = agent_runtime.run_turn
    session_runtime = SessionRuntime(
        chat_runtime=chat_runtime,
        chat_repo=chat_repo,
        agent_runtime=agent_runtime,
    )
    channel_session_semantics = ChannelSessionSemanticsRuntime()
    channel_ingress_runtime = ChannelIngressRuntime(
        session_runtime=session_runtime,
        channel_session_semantics=channel_session_semantics,
        chat_hook_runtime=chat_hook_runtime,
    )
    channel_session_context = ChannelSessionContext()
    channel_stream_bridge = ChannelStreamBridge()
    channel_approval_bridge = ChannelApprovalBridge()
    skill_candidate_extractor = SkillCandidateExtractor()
    skill_promotion_runtime = SkillPromotionRuntime()
    channel_runtime_context = ChannelRuntimeContext(
        provider_configs=provider_configs,
        channel_repo=channel_repo,
        chat_repo=chat_repo,
        media_repo=media_repo,
        chat_service=chat_service,
        notification_gateway_service=notification_gateway_service,
        channel_binding_service=channel_binding_service,
        channel_connector_registry=channel_connector_registry,
        artifact_store=artifact_store,
        secret_store=secret_store,
        trace_service=trace_service,
        audit_service=audit_service,
        session_runtime=session_runtime,
        channel_session_semantics=channel_session_semantics,
        channel_session_context=channel_session_context,
        channel_stream_bridge=channel_stream_bridge,
        channel_approval_bridge=channel_approval_bridge,
        channel_ingress_runtime=channel_ingress_runtime,
        data_dir=config.storage.data_dir,
        multimodal_understanding=multimodal_understanding_service,
    )
    channel_gateway_registry = ChannelGatewayRegistry()
    for provider, gateway in channel_extension_registry.build_gateways(channel_runtime_context).items():
        channel_gateway_registry.register(provider, gateway)
    wechat_gateway_service = channel_gateway_registry.require("wechat")
    feishu_gateway_service = channel_gateway_registry.require("feishu")
    chat_mainline_readiness_service = ChatMainlineReadinessService(
        root_dir=config.paths.root_dir,
        chat_runtime=chat_runtime,
        agent_runtime=agent_runtime,
        chat_service=chat_service,
        session_runtime=session_runtime,
        channel_session_semantics_runtime=channel_session_semantics,
        channel_ingress_runtime=channel_ingress_runtime,
        tool_runtime=tool_runtime,
        browser_workflow_runtime=browser_workflow_runtime,
        skill_plugin_service=skill_plugin_service,
        mcp_service=mcp_service,
        channel_gateway_registry=channel_gateway_registry,
        release_gate_service=release_gate_service,
        chat_run_ledger_service=chat_run_ledger_service,
        chat_hook_runtime=chat_hook_runtime,
    )
    release_gate_service.set_runtime_helpers(
        gate_runtime=release_gate_runtime,
        report_builder=release_report_builder,
        chat_mainline_readiness_service=chat_mainline_readiness_service,
    )
    if hasattr(wechat_gateway_service, "set_worker_health_provider"):
        wechat_gateway_service.set_worker_health_provider(background_worker_service.health)
    if hasattr(feishu_gateway_service, "set_worker_health_provider"):
        feishu_gateway_service.set_worker_health_provider(background_worker_service.health)
    background_worker_service.set_channel_gateway("wechat", wechat_gateway_service)
    background_worker_service.set_channel_gateway("feishu", feishu_gateway_service)
    control_plane_registry = ControlPlaneRegistry(
        routes={
            "chat": "/api/chat/*",
            "system": "/api/system/*",
        },
        startup={
            "bootstrap_service": "bootstrap_service",
            "shell_runtime": "shell_runtime",
        },
        workers={
            "background_worker_service": "background_worker_service",
            "scheduled_task_service": "scheduled_task_service",
        },
    )
    runtime_registry = RuntimePlaneRegistry(
        session_runtime=session_runtime,
        chat_runtime=chat_runtime,
        agent_runtime=agent_runtime,
        channel_ingress_runtime=channel_ingress_runtime,
    )
    capability_registry = CapabilityPlaneRegistry(
        browser_search_capability=chat_service._browser_search_capability,
        browser_research_runtime=chat_service._browser_research_runtime,
        tool_runtime=tool_runtime,
        skill_runtime=skill_plugin_service,
        mcp_runtime=mcp_service,
    )
    policy_registry = PolicyPlaneRegistry(
        persona_runtime=persona_runtime,
        heart_runtime=heart_runtime,
        tone_policy_runtime=tone_policy_runtime,
        response_quality_runtime=response_quality_runtime,
    )
    return ServiceRegistry(
        config=config,
        db=db,
        shell_runtime=shell_runtime,
        trace_service=trace_service,
        audit_service=audit_service,
        bootstrap_service=BootstrapService(
            db,
            shell_runtime,
            config.app.default_shell,
            persona_heart_service,
        ),
        chat_service=chat_service,
        chat_runtime=chat_runtime,
        agent_runtime=agent_runtime,
        session_runtime=session_runtime,
        channel_ingress_runtime=channel_ingress_runtime,
        channel_session_semantics_runtime=channel_session_semantics,
        chat_experience_service=chat_experience_service,
        chat_run_ledger_service=chat_run_ledger_service,
        failure_experience_service=failure_experience_service,
        chat_hook_runtime=chat_hook_runtime,
        agent_workbench_service=agent_workbench_service,
        memory_service=memory_service,
        media_service=media_service,
        video_workflow_service=video_workflow_service,
        asset_service=asset_service,
        asset_broker_service=asset_broker_service,
        capability_service=capability_service,
        knowledge_service=knowledge_service,
        task_engine=task_engine,
        goal_service=goal_service,
        background_worker_service=background_worker_service,
        scheduled_task_service=scheduled_task_service,
        checkpoint_service=checkpoint_service,
        notification_gateway_service=notification_gateway_service,
        channel_binding_service=channel_binding_service,
        channel_extension_registry=channel_extension_registry,
        channel_gateway_registry=channel_gateway_registry,
        wechat_gateway_service=wechat_gateway_service,
        feishu_gateway_service=feishu_gateway_service,
        browser_session_service=browser_session_service,
        autonomous_browser_workflow_service=browser_workflow_runtime,
        browser_workflow_runtime=browser_workflow_runtime,
        browser_session_runtime=browser_session_runtime,
        browser_page_state_runtime=browser_page_state_runtime,
        browser_replay_store=browser_replay_store,
        project_workspace_service=project_workspace_service,
        project_deployment_service=project_deployment_service,
        toolchain_service=toolchain_service,
        host_install_service=host_install_service,
        external_platform_action_service=external_platform_action_service,
        external_platform_adapter_service=external_platform_adapter_service,
        external_platform_extension_registry=external_platform_extension_registry,
        tool_runtime=tool_runtime,
        skill_governance_service=skill_governance_service,
        skill_plugin_service=skill_plugin_service,
        extension_service=extension_service,
        skill_repository_service=skill_repository_service,
        mcp_service=mcp_service,
        supervisor_service=supervisor_service,
        shell_switch_service=shell_switch_service,
        release_gate_service=release_gate_service,
        chat_mainline_readiness_service=chat_mainline_readiness_service,
        release_gate_runtime=release_gate_runtime,
        release_report_builder=release_report_builder,
        runtime_contract_service=runtime_contract_service,
        safety_policy_service=safety_policy_service,
        safety_decision_service=safety_decision_service,
        persona_heart_service=persona_heart_service,
        vector_service=vector_service,
        retrieval_service=RetrievalDiagnosticsService(repo=retrieval_repo),
        execution_boundary_service=execution_boundary_service,
        settings_service=settings_service,
        voice_service=voice_service,
        approval_service=approval_service,
        skill_candidate_extractor=skill_candidate_extractor,
        skill_promotion_runtime=skill_promotion_runtime,
        artifact_store=artifact_store,
        brain_service=BrainService(brain_repo, secret_store, audit_service),
        brain_decision_service=brain_decision_service,
        model_routing_service=model_routing_service,
        secret_store=secret_store,
        control_plane_registry=control_plane_registry,
        runtime_registry=runtime_registry,
        capability_registry=capability_registry,
        policy_registry=policy_registry,
        shells=ShellRepository(db),
        organization=organization_repo,
        members=member_repo,
        chat=chat_repo,
        agent_workbench=agent_workbench_repo,
        brains=brain_repo,
        memory=memory_repo,
        media=media_repo,
        assets=asset_repo,
        knowledge=knowledge_repo,
        tasks=task_repo,
        goals=goal_repo,
        voices=voice_repo,
        scheduled_tasks=scheduled_task_repo,
        checkpoints=checkpoint_repo,
        notifications=notification_repo,
        channels=channel_repo,
        browser=browser_repo,
        browser_workflows=browser_workflow_repo,
        project_deployments=project_deployment_repo,
        external_platform=external_platform_repo,
        external_platform_adapters=external_platform_adapter_repo,
        skill_governance=skill_governance_repo,
        skill_mcp=skill_mcp_repo,
        skill_repositories=skill_repository_repo,
        release=release_repo,
        retrieval=retrieval_repo,
        execution_boundary=execution_boundary_repo,
        design_alignment=design_alignment_repo,
    )
