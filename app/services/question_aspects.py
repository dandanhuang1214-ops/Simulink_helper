from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionAspect:
    name: str
    triggers: tuple[str, ...]
    facet: str
    evidence_terms: tuple[str, ...]
    context: tuple[str, ...] = ()
    answer_requirement: str = ""


# This is a small domain ontology, not a list of benchmark questions.  It maps
# stable engineering concepts to the English vocabulary used by the manuals.
# New domains can extend the registry without adding branches to the router.
ASPECT_REGISTRY = (
    QuestionAspect(
        "hierarchy_navigation",
        ("模型层级", "层级", "hierarchy", "model browser", "层级导航"),
        "Simulink model hierarchy Model Browser navigate subsystem parent system",
        ("model hierarchy", "model browser", "navigate", "subsystem", "parent system"),
        ("simulink", "模型", "model", "subsystem", "子系统"),
        "说明模型层级、子系统与层级导航的关系",
    ),
    QuestionAspect(
        "signal_tracing",
        ("追踪信号", "信号来源", "trace signal", "signal source", "highlight signal"),
        "Simulink trace signal source destination highlight to source",
        ("trace", "signal source", "highlight", "to source", "to destination"),
        answer_requirement="说明怎样沿连接关系追踪信号来源或去向",
    ),
    QuestionAspect(
        "simulation_configuration",
        ("停止时间", "块参数", "仿真参数", "stop time", "block parameter", "simulation parameter"),
        "Simulink simulation stop time block parameters configure model",
        ("stop time", "block parameter", "simulation parameter", "configuration parameters"),
        ("simulink", "仿真", "simulation", "模型", "model"),
        "区分仿真配置与具体块参数的作用",
    ),
    QuestionAspect(
        "simulation_execution",
        ("运行", "仿真", "simulate", "simulation", "run model"),
        "Simulink run simulation execute model",
        ("run simulation", "simulate", "simulation", "run the model"),
        ("simulink", "仿真", "simulation", "block", "模型"),
        "说明怎样运行模型或仿真",
    ),
    QuestionAspect(
        "result_inspection",
        ("查看结果", "看曲线", "data inspector", "inspect results", "view results", "plot"),
        "Simulink Data Inspector view simulation results plot signal",
        ("data inspector", "view simulation", "simulation results", "plot", "signal data"),
        answer_requirement="说明怎样查看、区分或检查仿真结果",
    ),
    QuestionAspect(
        "subsystem_encapsulation",
        ("封装", "subsystem", "子系统"),
        "Simulink subsystem hierarchy encapsulation interface ports component",
        ("subsystem", "hierarchy", "encapsulation", "interface", "input port", "output port"),
        ("simulink", "subsystem", "子系统", "模型"),
        "说明子系统的层级、封装和接口作用",
    ),
    QuestionAspect(
        "component_modeling",
        ("组件建模", "component modeling", "model component", "component behavior"),
        "AUTOSAR software component Simulink model component behavior implementation",
        ("software component", "model component", "component behavior", "simulink representation"),
        answer_requirement="说明 Simulink 如何承载 AUTOSAR 软件组件行为",
    ),
    QuestionAspect(
        "interface_mapping",
        (
            "接口映射", "端口映射", "端口", "数据元素", "接口", "映射",
            "interface mapping", "port mapping", "code mapping", "data element",
        ),
        "AUTOSAR code mappings Simulink elements ports runnable IRV interface mapping",
        ("mapping", "mapped", "port", "interface", "inter-runnable", "irv"),
        answer_requirement="说明 Simulink 元素如何映射到 AUTOSAR 端口、runnable 或 IRV",
    ),
    QuestionAspect(
        "code_generation",
        ("代码生成", "生成代码", "code generation", "generate code"),
        "Generate AUTOSAR C Code XML ARXML descriptions Embedded Coder",
        ("generate code", "generated code", "c code", "arxml", "xml descriptions", "embedded coder"),
        answer_requirement="说明代码生成与 ARXML 输出及其工具前提",
    ),
    QuestionAspect(
        "runnable_configuration",
        ("runnable", "可运行实体"),
        "AUTOSAR runnable executable entity entry-point function mapping configure RTE event",
        ("runnable", "entry-point function", "rte", "event", "schedulable entity"),
        answer_requirement="说明 runnable 的定义、入口函数映射与事件配置",
    ),
    QuestionAspect(
        "calibration_mapping",
        ("标定", "calibration", "calibratable", "lookup table"),
        "AUTOSAR calibration parameters lookup tables configure map Simulink model workspace",
        ("calibration", "calibratable", "lookup table", "model parameter", "code mappings", "swaddrmethod"),
        ("autosar", "标定", "calibration"),
        "说明标定参数的含义、配置位置与 AUTOSAR 映射",
    ),
    QuestionAspect(
        "state_decomposition",
        ("并行状态", "互斥状态", "parallel state", "exclusive state", "decomposition"),
        "Stateflow parallel exclusive state decomposition activation execution order",
        ("parallel", "exclusive", "decomposition", "active state", "execution order"),
        ("stateflow", "状态", "state"),
        "比较并行与互斥状态的激活和执行语义",
    ),
    QuestionAspect(
        "temporal_logic",
        ("after", "every", "持续时间", "temporal logic", "duration"),
        "Stateflow temporal logic after every duration elapsed time",
        ("temporal logic", "after", "every", "duration", "elapsed"),
        (),
        "区分不同时间逻辑算子的语义和用途",
    ),
    QuestionAspect(
        "state_history",
        ("history junction", "历史连接点", "状态恢复", "restore state"),
        "Stateflow history junction restore previous active state",
        ("history junction", "previously active", "restore", "historical state"),
        ("stateflow", "junction", "状态"),
        "说明历史连接点恢复活动状态的语义",
    ),
    QuestionAspect(
        "connective_junction",
        ("connective junction", "连接点", "junction"),
        "Stateflow connective junction transition path decision flow",
        ("connective junction", "transition path", "junction", "decision"),
        ("stateflow", "junction", "状态"),
        "说明普通连接点在转移路径中的作用",
    ),
    QuestionAspect(
        "chart_execution",
        ("chart 执行", "输入事件", "执行语义", "chart execution", "input event", "wake"),
        "Stateflow chart execution input event wake transition condition state action semantics",
        ("input event", "chart execution", "awakens", "transition condition", "active state", "action"),
        ("stateflow", "chart", "状态", "event", "事件"),
        "说明事件到达后 chart、状态、转移和动作的执行语义",
    ),
    QuestionAspect(
        "synchronization",
        ("同步", "synchronization", "synchronize", "rebuild", "push changes"),
        "Synchronize Changes Between Test Harness and Model push rebuild synchronization mode",
        ("synchronize", "synchronization", "push changes", "rebuild harness", "model to the harness", "harness to the model"),
        answer_requirement="说明 model 到 harness 的 rebuild 与 harness 到 model 的 push/同步方向",
    ),
    QuestionAspect(
        "harness_definition",
        ("是什么", "what is", "define", "definition"),
        "Test Harness and Model Relationship isolated environment component under test",
        ("test harness is", "isolated environment", "component under test", "harness-model relationship"),
        ("harness", "测试框架", "测试线束"),
        "明确说明 Test Harness 为被测组件提供隔离的测试环境",
    ),
    QuestionAspect(
        "harness_management",
        ("管理", "manage", "management", "内部", "外部", "internal", "external"),
        "Manage Test Harnesses saved internally externally association metadata",
        (
            "saved internally", "saved externally", "internally", "externally",
            "saveexternally", "internal harness", "external harness",
        ),
        ("harness", "测试框架", "测试线束"),
        "明确说明 Test Harness 可以内部或外部保存",
    ),
    QuestionAspect(
        "test_iterations",
        ("test iteration", "测试迭代", "多组参数", "parameter sets"),
        "Simulink Test iterations parameter sets results iteration",
        ("test iteration", "iterations", "parameter set", "iteration results"),
        ("test", "测试", "harness"),
        "说明测试迭代怎样承载多组参数并区分结果",
    ),
    QuestionAspect(
        "runtime_assessment",
        ("test assessment", "assessment", "运行时评估", "verify statement"),
        "Simulink Test Assessment run-time assessments verify statements",
        ("test assessment", "run-time assessment", "verify statement", "assesses"),
        ("test", "测试", "assessment"),
        "说明运行时 Assessment 验证的对象和判定方式",
    ),
    QuestionAspect(
        "baseline_comparison",
        ("基线比较", "baseline comparison", "baseline data", "baseline test"),
        "Simulink Test baseline test case compare simulation output baseline data tolerances",
        ("baseline test", "baseline data", "compare", "tolerance", "baseline criteria"),
        ("test", "测试", "baseline", "基线"),
        "说明基线比较怎样比较输出数据和容差",
    ),
    QuestionAspect(
        "test_reporting",
        ("测试报告", "汇总结果", "test report", "generate report", "test results"),
        "Simulink Test Manager results generate test report aggregate results",
        ("test manager", "test results", "generate report", "test report", "results and artifacts"),
        ("test", "测试", "test manager"),
        "说明测试结果汇总和报告生成流程",
    ),
    QuestionAspect(
        "coverage_accumulation",
        ("累计", "cumulative", "current run", "delta", "aggregate coverage"),
        "Simulink Coverage Current Run Delta Cumulative aggregate coverage results",
        ("current run", "delta", "cumulative", "accumulate", "aggregate"),
        ("coverage", "覆盖"),
        "说明覆盖率累计视图和各结果字段的含义",
    ),
    QuestionAspect(
        "instance_coverage",
        ("多个实例", "按实例", "多实例", "multiple instances", "per instance"),
        "Simulink Coverage multiple instances referenced model reusable subsystem per instance aggregated",
        ("multiple instances", "referenced model", "model block", "per instance", "reusable subsystem"),
        ("coverage", "覆盖", "引用模型", "referenced model", "subsystem"),
        "说明引用模型或可复用子系统多实例覆盖率的记录与限制",
    ),
    QuestionAspect(
        "variant_coverage",
        ("variant", "未激活", "inactive variant", "变体"),
        "Simulink Coverage inactive variants coverage report options",
        ("inactive variant", "variant", "coverage report", "exclude inactive"),
        ("coverage", "覆盖", "variant", "变体"),
        "说明未激活 Variant 对覆盖结果和报告的影响",
    ),
    QuestionAspect(
        "coverage_highlighting",
        ("高亮", "highlight", "模型上看", "model highlighting"),
        "Simulink Coverage model highlighting view coverage results",
        ("highlight", "model coloring", "coverage results", "model highlighting"),
        ("coverage", "覆盖"),
        "说明怎样在模型上查看覆盖率高亮",
    ),
    QuestionAspect(
        "coverage_reporting",
        ("覆盖率报告", "完整报告", "coverage report", "export report"),
        "Simulink Coverage generate export model coverage report",
        ("coverage report", "generate report", "export", "report"),
        ("coverage", "覆盖"),
        "说明怎样生成或导出覆盖率报告",
    ),
    QuestionAspect(
        "requirements_traceability",
        ("需求链接", "可追踪", "追溯", "requirements traceability", "requirement link"),
        "Simulink Test requirements traceability link test case coverage results",
        ("requirement", "traceability", "link", "test case", "coverage results"),
        ("test", "测试", "coverage", "覆盖", "需求", "requirement"),
        "说明需求、测试和覆盖率之间的可追踪关系",
    ),
    QuestionAspect(
        "harness_integration",
        ("放进 test harness", "放入 test harness", "connect a test harness", "containing test harness"),
        "Simulink Test connect component model test harness source sink interface function calls",
        ("test harness", "component under test", "source block", "sink block", "function calls", "connect"),
        ("test harness", "harness", "测试"),
        "说明组件接入 Test Harness 时的输入、输出和接口条件",
    ),
    QuestionAspect(
        "simulation_mode_coverage",
        ("normal", "sil", "pil", "模型覆盖率", "代码覆盖率", "simulation mode"),
        "Normal SIL PIL simulation mode model coverage code coverage interpretation",
        ("normal", "sil", "pil", "model coverage", "code coverage", "simulation mode"),
        ("coverage", "覆盖", "sil", "pil", "normal"),
        "区分不同仿真模式下模型覆盖率和代码覆盖率的含义",
    ),
)


def requested_aspects(question: str) -> list[QuestionAspect]:
    lowered = question.casefold()
    result: list[QuestionAspect] = []
    for aspect in ASPECT_REGISTRY:
        if aspect.context and not any(term in lowered for term in aspect.context):
            continue
        if any(trigger in lowered for trigger in aspect.triggers):
            result.append(aspect)
    return result


def aspect_query_facets(question: str) -> list[str]:
    return [aspect.facet for aspect in requested_aspects(question)]


def aspect_evidence_score(aspect: QuestionAspect, item: dict) -> float:
    heading = f"{item.get('title') or ''} {item.get('heading_path') or ''}".casefold()
    content = (item.get("content") or "").casefold()
    heading_hits = sum(term in heading for term in aspect.evidence_terms)
    content_hits = sum(term in content[:2400] for term in aspect.evidence_terms)
    # A heading match is more discriminative than a passing mention in a long
    # manual page. Scores are bounded and only used for deterministic coverage.
    return min(1.0, heading_hits * 0.34 + content_hits * 0.14)


def evidence_aspect_coverage(question: str, evidence: list[dict]) -> tuple[list[str], list[str]]:
    covered: list[str] = []
    missing: list[str] = []
    for aspect in requested_aspects(question):
        best = max((aspect_evidence_score(aspect, item) for item in evidence), default=0.0)
        (covered if best >= 0.28 else missing).append(aspect.name)
    return covered, missing
