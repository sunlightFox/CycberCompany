from __future__ import annotations

from dataclasses import dataclass

from shell_runtime import ShellRuntime
from trace_service import TraceService

from app.core.config import AppConfig
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
from app.db.session import Database
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
from app.services.capability import CapabilityGraphService
from app.services.channel_connectors import (
    ChannelConnectorRegistry,
    WechatClawbotConnector,
    WechatMockConnector,
)
from app.services.channels import ChannelBindingService
from app.services.chat import ChatService
from app.services.chat_experience import ChatExperienceService
from app.services.checkpoints import CheckpointService
from app.services.design_alignment import (
    PersonaHeartService,
    RuntimeContractService,
    SafetyDecisionService,
    VectorService,
)
from app.services.execution_boundary import ExecutionBoundaryService
from app.services.external_platform_actions import ExternalPlatformActionService
from app.services.external_platform_adapters import ExternalPlatformAdapterService
from app.services.knowledge import KnowledgeService
from app.services.mcp import MCPService
from app.services.media import MediaService
from app.services.memory import MemoryService
from app.services.model_routing import ModelRoutingService
from app.services.notifications import NotificationGatewayService
from app.services.office_tools import OfficeToolService
from app.services.project_deployments import (
    HostInstallService,
    ProjectDeploymentService,
    ProjectWorkspaceService,
    ToolchainService,
)
from app.services.release import ReleaseGateService
from app.services.retrieval import RetrievalDiagnosticsService
from app.services.scheduled_tasks import ScheduledTaskService
from app.services.secrets import SecretStore
from app.services.settings import SettingsService
from app.services.shell_switch import ShellSwitchService
from app.services.skill_governance import SkillGovernanceService
from app.services.skill_plugin import SkillPluginService
from app.services.skill_repositories import SkillRepositoryService
from app.services.skill_source_resolver import SkillSourceResolver
from app.services.supervisor import SupervisorService
from app.services.tasks import TaskEngine
from app.services.tools import ToolRuntime
from app.services.wechat_gateway import WechatChannelGatewayService


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
    media_service: MediaService
    asset_service: AssetService
    asset_broker_service: AssetBrokerService
    capability_service: CapabilityGraphService
    knowledge_service: KnowledgeService
    task_engine: TaskEngine
    background_worker_service: BackgroundWorkerService
    scheduled_task_service: ScheduledTaskService
    checkpoint_service: CheckpointService
    notification_gateway_service: NotificationGatewayService
    channel_binding_service: ChannelBindingService
    wechat_gateway_service: WechatChannelGatewayService
    browser_session_service: BrowserSessionService
    autonomous_browser_workflow_service: AutonomousBrowserWorkflowService
    project_workspace_service: ProjectWorkspaceService
    project_deployment_service: ProjectDeploymentService
    toolchain_service: ToolchainService
    host_install_service: HostInstallService
    external_platform_action_service: ExternalPlatformActionService
    external_platform_adapter_service: ExternalPlatformAdapterService
    tool_runtime: ToolRuntime
    skill_governance_service: SkillGovernanceService
    skill_plugin_service: SkillPluginService
    skill_repository_service: SkillRepositoryService
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
    media: MediaRepository
    assets: AssetRepository
    knowledge: KnowledgeRepository
    tasks: TaskRepository
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


def build_registry(config: AppConfig, db: Database, shell_runtime: ShellRuntime) -> ServiceRegistry:
    trace_service = TraceService(db)
    audit_service = AuditEventService(db)
    model_routing_service = ModelRoutingService(db, config.model_routing)
    secret_store = SecretStore(config.storage.data_dir / "secrets")
    brain_repo = BrainRepository(db)
    chat_repo = ChatRepository(db)
    member_repo = MemberRepository(db)
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
    media_service = MediaService(
        repo=media_repo,
        task_repo=task_repo,
        artifact_store=artifact_store,
        trace_service=trace_service,
        audit_service=audit_service,
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
        execution_boundary_service=execution_boundary_service,
        browser_session_service=browser_session_service,
        checkpoint_service=checkpoint_service,
        media_service=media_service,
        office_tool_service=office_tool_service,
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
    )
    skill_repository_service = SkillRepositoryService(
        repo=skill_repository_repo,
        config=config.skills,
        root_dir=config.paths.root_dir,
        trace_service=trace_service,
        audit_service=audit_service,
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
        source_resolver=skill_source_resolver,
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
    task_engine.set_browser_evidence_provider(browser_session_service.list_task_evidence)
    task_engine.set_checkpoint_replay_provider(checkpoint_service.replay_checkpoint_data)
    task_engine.set_media_replay_provider(media_service.replay_task_media)
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
    wechat_config = config.channels.providers.get("wechat")
    wechat_mock_config = config.channels.providers.get("wechat_mock")
    if wechat_config is None:
        from app.core.config import ChannelProviderSection

        wechat_config = ChannelProviderSection()
    if wechat_mock_config is None:
        from app.core.config import ChannelProviderSection

        wechat_mock_config = ChannelProviderSection(enabled=True, test_only=True)
    wechat_state_dir = wechat_config.state_dir or (
        config.storage.data_dir / "channel-providers" / "wechat"
    )
    channel_connector_registry = ChannelConnectorRegistry(
        [
            WechatClawbotConnector(wechat_config, state_dir=wechat_state_dir),
            WechatMockConnector(wechat_mock_config),
        ]
    )
    channel_binding_service = ChannelBindingService(
        repo=channel_repo,
        asset_repo=asset_repo,
        asset_service=asset_service,
        capability=capability_service,
        notifications=notification_gateway_service,
        connectors=channel_connector_registry,
        secret_store=secret_store,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    notification_gateway_service.register_provider(
        "wechat_mock",
        _ChannelNotificationProvider(channel_binding_service),
    )
    notification_gateway_service.register_provider(
        "wechat",
        _ChannelNotificationProvider(channel_binding_service),
    )
    approval_service.set_notification_callback(
        notification_gateway_service.notify_approval_required
    )
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
    scheduled_task_service.set_notification_callback(
        notification_gateway_service.notify_scheduled_run
    )
    background_worker_service = BackgroundWorkerService(
        scheduled_tasks=scheduled_task_service,
        notifications=notification_gateway_service,
        checkpoints=checkpoint_service,
        task_engine=task_engine,
        memory_service=memory_service,
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
    )
    external_platform_action_service = ExternalPlatformActionService(
        repo=external_platform_repo,
        asset_repo=asset_repo,
        asset_broker=asset_broker_service,
        task_engine=task_engine,
        approval_service=approval_service,
        trace_service=trace_service,
        audit_service=audit_service,
    )
    external_platform_adapter_service = ExternalPlatformAdapterService(
        repo=external_platform_adapter_repo,
        platform_repo=external_platform_repo,
        tool_runtime=tool_runtime,
        approval_service=approval_service,
        audit_service=audit_service,
    )
    autonomous_browser_workflow_service = AutonomousBrowserWorkflowService(
        repo=browser_workflow_repo,
        task_repo=task_repo,
        task_engine=task_engine,
        tool_runtime=tool_runtime,
        approval_service=approval_service,
        audit_service=audit_service,
    )
    chat_service = ChatService(
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
        project_deployment_service,
        host_install_service,
        skill_plugin_service,
        skill_governance_service,
        tool_runtime=tool_runtime,
    )
    wechat_gateway_service = WechatChannelGatewayService(
        repo=channel_repo,
        chat_repo=chat_repo,
        chat_service=chat_service,
        notifications=notification_gateway_service,
        connectors=channel_connector_registry,
        secret_store=secret_store,
        media_repo=media_repo,
        data_dir=config.storage.data_dir,
        trace_service=trace_service,
        audit_service=audit_service,
        config=wechat_config,
    )
    background_worker_service.set_wechat_gateway(wechat_gateway_service)
    return ServiceRegistry(
        config=config,
        db=db,
        shell_runtime=shell_runtime,
        trace_service=trace_service,
        audit_service=audit_service,
        bootstrap_service=BootstrapService(db, shell_runtime, config.app.default_shell),
        chat_service=chat_service,
        chat_experience_service=chat_experience_service,
        memory_service=memory_service,
        media_service=media_service,
        asset_service=asset_service,
        asset_broker_service=asset_broker_service,
        capability_service=capability_service,
        knowledge_service=knowledge_service,
        task_engine=task_engine,
        background_worker_service=background_worker_service,
        scheduled_task_service=scheduled_task_service,
        checkpoint_service=checkpoint_service,
        notification_gateway_service=notification_gateway_service,
        channel_binding_service=channel_binding_service,
        wechat_gateway_service=wechat_gateway_service,
        browser_session_service=browser_session_service,
        autonomous_browser_workflow_service=autonomous_browser_workflow_service,
        project_workspace_service=project_workspace_service,
        project_deployment_service=project_deployment_service,
        toolchain_service=toolchain_service,
        host_install_service=host_install_service,
        external_platform_action_service=external_platform_action_service,
        external_platform_adapter_service=external_platform_adapter_service,
        tool_runtime=tool_runtime,
        skill_governance_service=skill_governance_service,
        skill_plugin_service=skill_plugin_service,
        skill_repository_service=skill_repository_service,
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
        media=media_repo,
        assets=asset_repo,
        knowledge=knowledge_repo,
        tasks=task_repo,
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


class _ChannelNotificationProvider:
    def __init__(self, channels: ChannelBindingService) -> None:
        self._channels = channels

    async def send(self, *, channel, message):  # type: ignore[no-untyped-def]
        provider_state_ref = None
        if isinstance(channel.provider_config, dict):
            provider_state_ref = channel.provider_config.get("provider_state_ref")
        result = await self._channels.send_channel_text(
            provider=channel.provider,
            provider_state_ref=provider_state_ref,
            recipient=message.recipient,
            text=message.body_redacted,
        )
        from app.services.notifications import ProviderDeliveryResult

        return ProviderDeliveryResult(
            status=result.status,
            provider_message_id=result.provider_message_id,
            response_summary=result.response_summary,
            error_code=result.error_code,
            error_summary=result.error_summary,
        )
