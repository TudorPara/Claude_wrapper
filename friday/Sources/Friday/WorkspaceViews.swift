import Charts
import SwiftUI

/// Every command FRIDAY started and every ticket running anywhere, newest first.
struct ActivityView: View {
    @Environment(Store.self) private var store
    @State private var expanded: Set<UUID> = []

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Activity").font(.largeTitle.weight(.semibold))
                let running = store.tickets.filter(\.running)
                Block(title: "Agents running now", systemImage: "gearshape.2", tint: running.isEmpty ? .secondary : .blue) {
                    if running.isEmpty { Text("No ticket is running.").foregroundStyle(.secondary) }
                    ForEach(running) { summary in
                        Button {
                            store.select(project: summary.project, ticket: summary.id)
                        } label: {
                            HStack {
                                Text(summary.project).foregroundStyle(.secondary)
                                Text(summary.title)
                                Spacer()
                                StatusBadge(status: summary.status, running: true)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
                Block(title: "Commands from FRIDAY", systemImage: "terminal") {
                    if store.jobs.isEmpty { Text("Nothing started from FRIDAY yet.").foregroundStyle(.secondary) }
                    ForEach(store.jobs.reversed()) { job in
                        JobRow(job: job, expanded: expanded.contains(job.id)) {
                            if expanded.contains(job.id) { expanded.remove(job.id) } else { expanded.insert(job.id) }
                        }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
        }
        .navigationTitle("Activity")
        .toolbar { WorkspaceToolbar(project: store.selectedProject) }
    }
}

struct JobRow: View {
    @Environment(Store.self) private var store
    let job: Job
    var expanded = false
    var toggle: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if job.running {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: job.failed ? "xmark.octagon.fill" : "checkmark.circle.fill").foregroundStyle(job.failed ? .red : .green)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(job.title).font(.callout.weight(.medium))
                    Text("cw " + job.arguments.joined(separator: " ")).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Text(job.startedAt, format: .relative(presentation: .named)).font(.caption).foregroundStyle(.tertiary)
                if let id = job.ticketId {
                    Button("Open ticket") { store.select(project: store.summary(id)?.project ?? job.project, ticket: id) }
                }
                if job.running { Button("Stop", role: .destructive) { store.stop(job) } }
                Button(expanded ? "Hide output" : "Output", action: toggle)
            }
            if expanded {
                LogView(text: job.log)
            } else if !job.lastLine.isEmpty {
                Text(job.lastLine).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.vertical, 4)
    }
}

/// Tokens and cost per project and agent role, from `cw stats --json`.
struct UsageView: View {
    @Environment(Store.self) private var store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("Usage").font(.largeTitle.weight(.semibold))
                    Spacer()
                    Button("Refresh") { Task { await store.refreshUsage() } }
                }
                if store.usage.isEmpty {
                    ContentUnavailableView("No runs yet", systemImage: "chart.bar", description: Text("Agent runs show up here once a ticket has started."))
                } else {
                    HStack {
                        MetricTile(title: "Spent", value: store.usage.map(\.costUsd).reduce(0, +).dollars, systemImage: "dollarsign.circle")
                        MetricTile(title: "Runs", value: "\(store.usage.map(\.runs).reduce(0, +))", systemImage: "play.circle")
                        MetricTile(title: "Tokens out", value: store.usage.map(\.outputTokens).reduce(0, +).compact, systemImage: "text.bubble")
                        MetricTile(title: "Read from cache", value: store.usage.map(\.cacheReadTokens).reduce(0, +).compact, systemImage: "memorychip")
                    }
                    Block(title: "Cost per project", systemImage: "chart.bar") {
                        Chart(store.usage) { row in
                            BarMark(x: .value("Cost", row.costUsd), y: .value("Project", row.project))
                                .foregroundStyle(by: .value("Role", row.role))
                        }
                        .chartForegroundStyleScale(["planner": Color.purple, "worker": Color.blue, "reviewer": Color.orange, "onboarder": Color.teal])
                        .frame(height: CGFloat(Set(store.usage.map(\.project)).count) * 40 + 40)
                    }
                    Table(store.usage) {
                        TableColumn("Project", value: \.project)
                        TableColumn("Role", value: \.role)
                        TableColumn("Runs") { Text("\($0.runs)").monospacedDigit() }
                        TableColumn("In") { Text($0.inputTokens.compact).monospacedDigit() }
                        TableColumn("Out") { Text($0.outputTokens.compact).monospacedDigit() }
                        TableColumn("Cached") { Text($0.cacheReadTokens.compact).monospacedDigit() }
                        TableColumn("Cost") { Text($0.costUsd.dollars).monospacedDigit() }
                    }
                    .frame(minHeight: CGFloat(store.usage.count) * 26 + 34)
                }
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
        }
        .navigationTitle("Usage")
        .toolbar { WorkspaceToolbar(project: store.selectedProject) }
        .task { await store.refreshUsage() }
    }
}

/// `cw doctor --json`: what cw needs on this Mac, with the fix for anything missing.
struct DoctorCard: View {
    @Environment(Store.self) private var store
    @State private var checking = false

    var body: some View {
        Card(title: store.doctorProblems.isEmpty ? "Health: all good" : "Health: \(store.doctorProblems.count) to fix", systemImage: "stethoscope") {
            ForEach(store.doctor) { row in
                HStack(alignment: .firstTextBaseline) {
                    Image(systemName: row.isOK ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(row.isOK ? .green : .orange)
                    Text(row.check)
                    Spacer()
                    if !row.isOK { Text(row.hint).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                }
            }
            Button(checking ? "Checking…" : "Check again") {
                checking = true
                Task {
                    await store.refreshDoctor()
                    checking = false
                }
            }
            .disabled(checking)
        }
    }
}
