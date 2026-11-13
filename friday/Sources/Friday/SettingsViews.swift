import SwiftUI

/// Models per agent and every loop and worker limit. Used for a project and for the defaults new projects copy.
struct ProfileEditor: View {
    @Environment(Store.self) private var store
    @Binding var profile: Profile
    var availableModels: [String]
    var permissionModes: [String]
    var pinnedStackSkills: [String]

    var body: some View {
        Card(title: "Agents and models", systemImage: "person.3") {
            Text("Each agent is a markdown file in the toolkit. Pick the model it runs on here; the file stays the same for the whole team.")
                .font(.caption)
                .foregroundStyle(.secondary)
            ForEach(AgentRole.allCases) { role in
                let agent = store.toolkit?.agents.first { $0.name == role.rawValue }
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(role.label).fontWeight(.semibold)
                        Text(agent?.description ?? "").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        let skills = (agent?.skills ?? []) + pinnedStackSkills
                        Text("Tools: \(agent.map { $0.tools.isEmpty ? "all" : $0.tools.joined(separator: ", ") } ?? "?")  ·  Skills: \(skills.isEmpty ? "none pinned" : skills.joined(separator: ", "))")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Picker("", selection: $profile.models[role]) {
                        ForEach(Array(Set(availableModels + [profile.models[role]])).sorted(), id: \.self) { Text($0).tag($0) }
                    }
                    .frame(width: 130)
                    Stepper("\(profile.limits.maxTurns[role]) turns", value: $profile.limits.maxTurns[role], in: 1...500, step: 5)
                        .frame(width: 140)
                        .help("Maximum agentic turns for one \(role.rawValue) run")
                }
                Divider()
            }
            HStack {
                Text("Reviewers")
                TextField("reviewer", text: Binding(
                    get: { profile.reviewers.joined(separator: ", ") },
                    set: { profile.reviewers = $0.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
                ))
                Text("All must approve. Each extra reviewer is another run.").font(.caption).foregroundStyle(.secondary)
            }
            Picker("Worker permission mode", selection: $profile.workerPermissionMode) {
                ForEach(Array(Set(permissionModes + [profile.workerPermissionMode])).sorted(), id: \.self) { Text($0).tag($0) }
            }
            .frame(maxWidth: 420)
        }
        Card(title: "Loops (each is a cap, not a target)", systemImage: "arrow.triangle.2.circlepath") {
            LabeledStepper(title: "Review rounds", help: "Worker fixes after the reviewer asks for changes", value: $profile.limits.maxReviewRounds, range: 0...10)
            LabeledStepper(title: "Fix rounds", help: "Worker fixes after lint or tests fail", value: $profile.limits.maxFixRounds, range: 0...10)
            LabeledStepper(title: "Conflict rounds", help: "Attempts to resolve conflicts with the base branch", value: $profile.limits.maxConflictRounds, range: 0...5)
            LabeledStepper(title: "Question rounds", help: "Times the planner may ask you, and a worker may ask per instruction", value: $profile.limits.maxQuestionRounds, range: 0...5)
            LabeledStepper(title: "Escalation threshold", help: "A direct task touching more files goes to the planner", value: $profile.limits.escalateFileThreshold, range: 1...50)
            LabeledStepper(title: "Summary lines", help: "Longest summary an agent writes into the ticket", value: $profile.limits.summaryMaxLines, range: 3...60)
            LabeledStepper(title: "Diff characters", help: "Longest diff sent to the reviewer", value: $profile.limits.maxDiffChars, range: 5_000...400_000, step: 5_000)
            LabeledStepper(title: "Check output characters", help: "Longest lint/test output sent back to the worker", value: $profile.limits.maxCheckOutputChars, range: 500...40_000, step: 500)
            Group {
                LabeledStepper(
                    title: "Workers", help: "Most workers the planner may spread a plan over; they take turns",
                    value: Binding(get: { profile.limits.maxWorkers ?? 4 }, set: { profile.limits.maxWorkers = $0 }), range: 1...8)
                LabeledStepper(
                    title: "Agent time limit (minutes)", help: "An agent run that takes longer is stopped",
                    value: Binding(get: { profile.limits.agentTimeoutMinutes ?? 45 }, set: { profile.limits.agentTimeoutMinutes = $0 }), range: 1...240)
                LabeledStepper(
                    title: "Check time limit (minutes)", help: "A lint or test command that takes longer counts as failed",
                    value: Binding(get: { profile.limits.checkTimeoutMinutes ?? 15 }, set: { profile.limits.checkTimeoutMinutes = $0 }), range: 1...120)
            }
        }
    }
}

struct ProjectSettingsView: View {
    @Environment(Store.self) private var store
    var project: Project
    @State private var draft: Project?

    var body: some View {
        ScrollView {
            if let config = store.config, draft != nil {
                let binding = Binding(get: { draft! }, set: { draft = $0 })
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Changes are validated by cw before they are saved.").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("Map this project") {
                            store.run("Map \(project.name)", project: project.name, ticketId: nil, ["project", "onboard", project.name])
                        }
                        Button("Apply models & limits to all projects") { save(config, applyProfileToAll: true) }
                        Button("Save") { save(config, applyProfileToAll: false) }
                            .buttonStyle(.borderedProminent)
                            .disabled(draft == project)
                            .keyboardShortcut("s")
                    }
                    Card(title: "Repository", systemImage: "externaldrive.connected.to.line.below") {
                        Picker("Code host", selection: binding.gitHost) {
                            Text("GitHub").tag("github")
                            Text("Bitbucket Cloud").tag("bitbucket")
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 360)
                        TextField("Base branch", text: binding.baseBranch)
                        TextField("Remote", text: binding.remote)
                        Picker("Stack", selection: binding.stack) {
                            ForEach(Array(Set(Array(config.stacks.keys) + [binding.wrappedValue.stack])).sorted(), id: \.self) {
                                Text($0.isEmpty ? "none" : $0).tag($0)
                            }
                        }
                        .frame(maxWidth: 300)
                    }
                    Card(title: "Checks before review", systemImage: "checklist") {
                        TextField("Lint", text: binding.commands.lint)
                        TextField("Test", text: binding.commands.test)
                        Text("Run by cw after every worker pass, before any reviewer sees the code. Empty skips the step.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    ProfileEditor(
                        profile: binding.profile,
                        availableModels: config.availableModels,
                        permissionModes: config.permissionModes,
                        pinnedStackSkills: config.stacks[binding.wrappedValue.stack]?.pinnedSkills ?? [])
                    Card(title: "Safety and environment", systemImage: "lock.shield") {
                        LinesEditor(title: "Protected paths", help: "Agents can't read or edit these; shell commands naming them are blocked. A guard rail, not a wall.", lines: binding.protectedPaths)
                        LinesEditor(title: "Extra allowed shell prefixes", help: "The worker may run these without asking (lint, test and setup are always allowed).", lines: binding.extraAllowedBash)
                        KeyValueEditor(title: "Environment", help: "KEY=value per line; {ticket} becomes the ticket id, e.g. COMPOSE_PROJECT_NAME=app-{ticket}.", values: binding.env)
                    }
                }
                .padding(20)
            } else {
                ProgressView().padding(40)
            }
        }
        .onAppear { draft = project }
        .onChange(of: project) { draft = project }
    }

    private func save(_ config: CWConfig, applyProfileToAll: Bool) {
        guard let draft else { return }
        var updated = config
        updated.projects = config.projects.map { existing in
            if existing.name == project.name { return draft }
            var other = existing
            if applyProfileToAll { other.profile = draft.profile }
            return other
        }
        Task { await store.save(updated) }
    }
}

/// Chat, voice and look. Saved at once on this Mac; not part of cw's config.
struct ChatPreferencesCard: View {
    @AppStorage(Preference.chatTextSize) private var textSize = Preference.defaultChatTextSize
    @AppStorage(Preference.liveOutputLines) private var liveLines = Preference.defaultLiveOutputLines
    @AppStorage(Preference.planFirstByDefault) private var planFirst = false
    @AppStorage(Preference.speakReplies) private var speakReplies = false
    @AppStorage(Preference.voiceLanguage) private var voiceLanguage = Preference.defaultVoiceLanguage
    @AppStorage(Preference.accent) private var accent = Preference.defaultAccent

    var body: some View {
        Card(title: "Chat, voice and look", systemImage: "bubble.left.and.bubble.right") {
            HStack {
                Text("Chat text size")
                Slider(value: $textSize, in: 11...18, step: 1).frame(maxWidth: 220)
                Text("\(Int(textSize)) pt").font(.caption).foregroundStyle(.secondary)
            }
            Stepper("Live output lines shown in the chat: \(liveLines)", value: $liveLines, in: 1...20)
            Toggle("Plan first by default for new tickets", isOn: $planFirst)
            Toggle("Speak replies: FRIDAY says where a ticket stands when a step ends", isOn: $speakReplies)
            HStack {
                Text("Voice language")
                TextField("en-US", text: $voiceLanguage).frame(maxWidth: 120)
                Text("A locale such as en-US or ro-RO, used for dictation and speech.").font(.caption).foregroundStyle(.secondary)
            }
            Picker("Accent colour", selection: $accent) {
                ForEach(Theme.accents.keys.sorted(), id: \.self) { Text($0.capitalized).tag($0) }
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 360)
            Text("Saved at once, on this Mac only. Dictation works when FRIDAY runs as the app (FRIDAY.app) and you allow the microphone and speech recognition.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var draft: CWConfig?

    var body: some View {
        @Bindable var store = store
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Settings").font(.largeTitle.bold())
                    Spacer()
                    Button("Save") { if let draft { Task { await store.save(draft) } } }
                        .buttonStyle(.borderedProminent)
                        .disabled(draft == nil || draft == store.config)
                }
                Card(title: "This Mac", systemImage: "desktopcomputer") {
                    TextField("cw executable", text: $store.cwExecutable)
                    TextField("CW_HOME (empty = ~/.cw)", text: $store.cwHome)
                    Text("FRIDAY runs cw through your login shell, so claude, gh and uv resolve as in Terminal.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Reload") { Task { await store.refreshAll(); draft = store.config } }
                }
                ChatPreferencesCard()
                if draft != nil {
                    let binding = Binding(get: { draft! }, set: { draft = $0 })
                    Card(title: "Toolkit", systemImage: "shippingbox") {
                        TextField("Local clone", text: binding.toolkit.path)
                        TextField("GitHub repository (owner/name)", text: binding.toolkit.githubRepo)
                        TextField("Marketplace name", text: binding.toolkit.marketplace)
                        LinesEditor(title: "Models offered in pickers", help: "Aliases (sonnet, opus, haiku, fable) or full model ids.", lines: binding.availableModels)
                    }
                    Text("Defaults for new projects").font(.title3.bold()).padding(.top, 6)
                    ProfileEditor(
                        profile: binding.defaults,
                        availableModels: binding.wrappedValue.availableModels,
                        permissionModes: binding.wrappedValue.permissionModes,
                        pinnedStackSkills: [])
                    Card(title: "Defaults for new projects: safety", systemImage: "lock.shield") {
                        LinesEditor(title: "Protected paths", help: "Copied into each new project.", lines: binding.defaultProtectedPaths)
                    }
                }
            }
            .padding(24)
        }
        .onAppear { draft = store.config }
        .onChange(of: store.config) { draft = store.config }
    }
}
