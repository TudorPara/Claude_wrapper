import Foundation

// Mirrors cw's config.json exactly: FRIDAY reads it with `cw config --json` and saves it with `cw config set`.

struct RoleModels: Codable, Equatable {
    var planner: String
    var worker: String
    var reviewer: String
    var onboarder: String

    subscript(role: AgentRole) -> String {
        get {
            switch role {
            case .planner: planner
            case .worker: worker
            case .reviewer: reviewer
            case .onboarder: onboarder
            }
        }
        set {
            switch role {
            case .planner: planner = newValue
            case .worker: worker = newValue
            case .reviewer: reviewer = newValue
            case .onboarder: onboarder = newValue
            }
        }
    }
}

struct RoleTurns: Codable, Equatable {
    var planner: Int
    var worker: Int
    var reviewer: Int
    var onboarder: Int

    subscript(role: AgentRole) -> Int {
        get {
            switch role {
            case .planner: planner
            case .worker: worker
            case .reviewer: reviewer
            case .onboarder: onboarder
            }
        }
        set {
            switch role {
            case .planner: planner = newValue
            case .worker: worker = newValue
            case .reviewer: reviewer = newValue
            case .onboarder: onboarder = newValue
            }
        }
    }
}

struct Limits: Codable, Equatable {
    var maxReviewRounds: Int
    var maxFixRounds: Int
    var maxConflictRounds: Int
    var maxQuestionRounds: Int
    var escalateFileThreshold: Int
    var summaryMaxLines: Int
    var maxDiffChars: Int
    var maxCheckOutputChars: Int
    var maxTurns: RoleTurns

    enum CodingKeys: String, CodingKey {
        case maxReviewRounds = "max_review_rounds"
        case maxFixRounds = "max_fix_rounds"
        case maxConflictRounds = "max_conflict_rounds"
        case maxQuestionRounds = "max_question_rounds"
        case escalateFileThreshold = "escalate_file_threshold"
        case summaryMaxLines = "summary_max_lines"
        case maxDiffChars = "max_diff_chars"
        case maxCheckOutputChars = "max_check_output_chars"
        case maxTurns = "max_turns"
    }
}

struct Profile: Codable, Equatable {
    var models: RoleModels
    var limits: Limits
    var workerPermissionMode: String
    var reviewers: [String]

    enum CodingKeys: String, CodingKey {
        case models, limits, reviewers
        case workerPermissionMode = "worker_permission_mode"
    }
}

struct ProjectCommands: Codable, Equatable {
    var lint: String
    var test: String
}

struct Project: Codable, Equatable, Identifiable, Hashable {
    var name: String
    var path: String
    var baseBranch: String
    var remote: String
    var stack: String
    var gitHost: String
    var commands: ProjectCommands
    var protectedPaths: [String]
    var extraAllowedBash: [String]
    var env: [String: String]
    var profile: Profile

    var id: String { name }

    static func == (lhs: Project, rhs: Project) -> Bool {
        lhs.name == rhs.name && lhs.path == rhs.path && lhs.baseBranch == rhs.baseBranch && lhs.remote == rhs.remote
            && lhs.stack == rhs.stack && lhs.gitHost == rhs.gitHost && lhs.commands == rhs.commands
            && lhs.protectedPaths == rhs.protectedPaths
            && lhs.extraAllowedBash == rhs.extraAllowedBash && lhs.env == rhs.env && lhs.profile == rhs.profile
    }

    func hash(into hasher: inout Hasher) { hasher.combine(name) }

    enum CodingKeys: String, CodingKey {
        case name, path, remote, stack, commands, env, profile
        case baseBranch = "base_branch"
        case gitHost = "git_host"
        case protectedPaths = "protected_paths"
        case extraAllowedBash = "extra_allowed_bash"
    }
}

struct StackSettings: Codable, Equatable {
    var markers: [String]
    var toolkitPlugins: [String]
    var pinnedSkills: [String]
    var externalPlugins: [String]
    var commands: ProjectCommands

    enum CodingKeys: String, CodingKey {
        case markers, commands
        case toolkitPlugins = "toolkit_plugins"
        case pinnedSkills = "pinned_skills"
        case externalPlugins = "external_plugins"
    }
}

struct ToolkitSettings: Codable, Equatable {
    var path: String
    var marketplace: String
    var githubRepo: String
    var alwaysPlugins: [String]
    var alwaysExternalPlugins: [String]

    enum CodingKeys: String, CodingKey {
        case path, marketplace
        case githubRepo = "github_repo"
        case alwaysPlugins = "always_plugins"
        case alwaysExternalPlugins = "always_external_plugins"
    }
}

struct CWConfig: Codable, Equatable {
    var claudeBinary: String
    var availableModels: [String]
    var permissionModes: [String]
    var defaultProtectedPaths: [String]
    var toolkit: ToolkitSettings
    var externalMarketplaces: [String: String]
    var stacks: [String: StackSettings]
    var defaults: Profile
    var projects: [Project]

    enum CodingKeys: String, CodingKey {
        case toolkit, stacks, defaults, projects
        case claudeBinary = "claude_binary"
        case availableModels = "available_models"
        case permissionModes = "permission_modes"
        case defaultProtectedPaths = "default_protected_paths"
        case externalMarketplaces = "external_marketplaces"
    }
}

enum AgentRole: String, CaseIterable, Identifiable {
    case planner, worker, reviewer, onboarder
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

// Tickets, as printed by `cw status --json` and `cw show <id> --json`.

enum TicketStatus: String, Codable {
    case needsInput = "needs_input"
    case awaitingApproval = "awaiting_approval"
    case inProgress = "in_progress"
    case ready
    case inReview = "in_review"
    case blocked
    case paused
    case prOpen = "pr_open"
    case done
    case abandoned

    var label: String {
        switch self {
        case .needsInput: "Needs your answer"
        case .awaitingApproval: "Waiting for approval"
        case .inProgress: "In progress"
        case .ready: "Ready"
        case .inReview: "In review"
        case .blocked: "Blocked"
        case .paused: "Paused"
        case .prOpen: "PR open"
        case .done: "Done"
        case .abandoned: "Abandoned"
        }
    }

    var needsYou: Bool { [.needsInput, .awaitingApproval, .blocked, .paused, .ready].contains(self) }
}

struct TicketSummary: Codable, Identifiable, Hashable {
    var id: String
    var project: String
    var title: String
    var status: TicketStatus
    var route: String
    var branch: String
    var prUrl: String?
    var updatedAt: String
    var running: Bool
    var archived: Bool
    var error: String?

    enum CodingKeys: String, CodingKey {
        case id, project, title, status, route, branch, running, archived, error
        case prUrl = "pr_url"
        case updatedAt = "updated_at"
    }
}

struct PromptEntry: Codable, Hashable { var at: String; var text: String }

struct Subtask: Codable, Hashable {
    var id: String
    var title: String
    var instructions: String
    var files: [String]
}

struct WorkEntry: Codable, Hashable {
    var at: String
    var subtask: String?
    var status: String
    var summary: String
}

struct ReviewComment: Codable, Hashable {
    var path: String
    var line: Int
    var severity: String
    var body: String
}

struct ReviewEntry: Codable, Hashable {
    var at: String
    var round: Int
    var reviewer: String
    var verdict: String
    var summary: String
    var comments: [ReviewComment]
}

struct RunUsage: Codable, Hashable {
    var at: String
    var role: String
    var agent: String
    var model: String
    var inputTokens: Int
    var outputTokens: Int
    var cacheReadTokens: Int
    var cacheCreationTokens: Int
    var costUsd: Double
    var durationMs: Int
    var numTurns: Int

    enum CodingKeys: String, CodingKey {
        case at, role, agent, model
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case cacheReadTokens = "cache_read_tokens"
        case cacheCreationTokens = "cache_creation_tokens"
        case costUsd = "cost_usd"
        case durationMs = "duration_ms"
        case numTurns = "num_turns"
    }
}

struct Ticket: Codable, Identifiable {
    var id: String
    var project: String
    var title: String
    var status: TicketStatus
    var route: String
    var branch: String
    var baseBranch: String
    var previousBranch: String?
    var tasksDone: [String]?
    var owner: String
    var prompts: [PromptEntry]
    var questions: [String]
    var plan: String
    var optionsConsidered: String
    var subtasks: [Subtask]
    var workLog: [WorkEntry]
    var reviews: [ReviewEntry]
    var prUrl: String?
    var error: String?
    var runs: [RunUsage]
    var archived: Bool?
    var warnings: [String]?
    var split: [String]?
    var pushedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, project, title, status, route, branch, owner, prompts, questions, plan, subtasks, reviews,
            error, runs, archived
        case baseBranch = "base_branch"
        case previousBranch = "previous_branch"
        case tasksDone = "tasks_done"
        case optionsConsidered = "options_considered"
        case workLog = "work_log"
        case prUrl = "pr_url"
        case warnings, split
        case pushedAt = "pushed_at"
    }
}

// The toolkit inventory from `cw toolkit --json`.

struct AgentDefinition: Codable, Identifiable, Hashable {
    var name: String
    var plugin: String
    var description: String
    var tools: [String]
    var model: String
    var skills: [String]
    var path: String
    var id: String { "\(plugin):\(name)" }
}

struct SkillDefinition: Codable, Identifiable, Hashable {
    var name: String
    var plugin: String
    var description: String
    var modelInvocable: Bool
    var path: String
    var id: String { "\(plugin):\(name)" }

    enum CodingKeys: String, CodingKey {
        case name, plugin, description, path
        case modelInvocable = "model_invocable"
    }
}

struct ToolkitInventory: Codable {
    var path: String
    var version: String
    var marketplace: String
    var alwaysPlugins: [String]
    var alwaysExternalPlugins: [String]
    var agents: [AgentDefinition]
    var skills: [SkillDefinition]
    var stacks: [String: StackSettings]

    enum CodingKeys: String, CodingKey {
        case path, version, marketplace, agents, skills, stacks
        case alwaysPlugins = "always_plugins"
        case alwaysExternalPlugins = "always_external_plugins"
    }
}

struct UsageRow: Codable, Identifiable, Hashable {
    var project: String
    var role: String
    var runs: Int
    var inputTokens: Int
    var outputTokens: Int
    var cacheReadTokens: Int
    var costUsd: Double
    var id: String { "\(project)/\(role)" }

    enum CodingKeys: String, CodingKey {
        case project, role, runs
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case cacheReadTokens = "cache_read_tokens"
        case costUsd = "cost_usd"
    }
}

/// One codebase as `cw project list --json` reports it.
struct ProjectInfo: Codable, Identifiable, Hashable {
    var name: String
    var path: String
    var exists: Bool
    var branch: String
    var dirty: Bool
    var lastCommit: String
    var remoteUrl: String
    var conventions: Bool
    var openTickets: Int
    var running: Int
    var waiting: Int
    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, path, exists, branch, dirty, conventions, running, waiting
        case lastCommit = "last_commit"
        case remoteUrl = "remote_url"
        case openTickets = "open_tickets"
    }
}
