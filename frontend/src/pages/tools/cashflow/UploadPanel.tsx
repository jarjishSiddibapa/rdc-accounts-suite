import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  ListChecks,
  RefreshCw,
  Settings2,
  UploadCloud,
} from "lucide-react";
import { GlassCard } from "@/components/GlassCard";
import { Button } from "@/components/Button";
import { ErrorBanner } from "@/components/ErrorBanner";
import { FileDropzone } from "@/components/FileDropzone";
import { ProgressPanel } from "@/components/ProgressPanel";
import { CreatableCombobox } from "@/components/CreatableCombobox";
import { LoadingNotice } from "@/components/LoadingNotice";
import { Pagination } from "@/components/Pagination";
import { usePagination } from "@/hooks/usePagination";
import { ApiError, apiUrl, get, post, postForm } from "@/lib/api";
import { formatIndianDateTime, formatIndianNumber } from "@/lib/regional";
import {
  BASE,
  ISSUE_TEXT,
  monthLabel,
  money,
  pollJob,
  type GenerateOutcome,
  type MappingOptions,
  type MonthRow,
  type Unmapped,
  type UnmappedOther,
} from "./shared";
import { EXCEL_ACCEPT } from '@/lib/fileTypes'

interface UploadFileResult {
  file: string;
  ok: boolean;
  error?: string;
  account_no?: string;
  line_count?: number;
  new_count?: number;
  duplicate_count?: number;
  header_warnings?: string[];
}

interface UploadResult {
  files: UploadFileResult[];
  months: GenerateOutcome[];
}

function StatusChip({
  tone,
  children,
}: {
  tone: "ok" | "warn" | "muted";
  children: React.ReactNode;
}) {
  const styles = {
    ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
    warn: "border-amber-500/30 bg-amber-500/10 text-amber-600",
    muted: "border-border bg-bg-soft/60 text-ink-dim",
  }[tone];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${styles}`}
    >
      {children}
    </span>
  );
}

// ── fix-up panel for one month ───────────────────────────────────────────

interface KnownParty {
  name: string;
  location: string;
  effective_cf_location: string;
  sharing_group: string;
}

/** One "other" issue: a name still missing its location / CF location is fixed right here. */
function OtherIssue({
  item,
  options,
  onFixed,
}: {
  item: UnmappedOther;
  options: MappingOptions;
  onFixed: () => void;
}) {
  const [location, setLocation] = useState("");
  const [cf, setCf] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lines = `${formatIndianNumber(item.count)} line${item.count === 1 ? "" : "s"}`;
  const needsLocation = item.issue === "no_location";
  const fixableHere =
    item.source === "party" &&
    Boolean(item.name) &&
    (needsLocation || item.issue === "no_cf_location");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await post(`${BASE}/mappings/fix-party`, {
        party: item.name,
        location: needsLocation ? location || null : null,
        cf_location: cf || null,
      });
      onFixed();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the mapping.");
    } finally {
      setBusy(false);
    }
  }

  const where =
    item.source === "split"
      ? "Fix the typed split under Review lines."
      : item.source === "ratio"
        ? "Fix the sharing ratio's CF location under Mappings → Sharing ratios."
        : "Fix it under Mappings.";

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-ink">
        <strong>{item.name || item.sample}</strong> — {ISSUE_TEXT[item.issue] ?? item.issue} ({lines})
        {fixableHere ? "" : `. ${where}`}
      </p>
      {error && <ErrorBanner>{error}</ErrorBanner>}
      {fixableHere && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          {needsLocation && (
            <CreatableCombobox
              placeholder="Location"
              ariaLabel={`Location for ${item.name}`}
              value={location}
              options={options.locations}
              suggestionLabel="Existing locations"
              onChange={setLocation}
              className="w-full sm:w-64"
            />
          )}
          <CreatableCombobox
            placeholder="CF location"
            ariaLabel={`CF location for ${item.name}`}
            value={cf}
            options={options.cf_locations}
            suggestionLabel="CF locations"
            onChange={setCf}
            className="w-full sm:w-64"
          />
          <Button
            variant="secondary"
            loading={busy}
            disabled={!cf.trim() || (needsLocation && !location.trim())}
            onClick={() => void save()}
          >
            Save
          </Button>
        </div>
      )}
    </div>
  );
}

function FixPanel({
  month,
  options,
  onChanged,
  onGenerate,
  onDownloadAnyway,
  onReview,
  generating,
}: {
  month: string;
  options: MappingOptions;
  onChanged: () => void;
  onGenerate: (month: string) => void;
  onDownloadAnyway: (month: string) => void;
  onReview: (month: string) => void;
  generating: boolean;
}) {
  const [data, setData] = useState<Unmapped | null>(null);
  const [knownParties, setKnownParties] = useState<KnownParty[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [forms, setForms] = useState<
    Record<string, { party: string; location: string; cf_location: string }>
  >({});
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const rows = data?.no_party ?? [];
  const pagination = usePagination(rows, 10);

  const load = useCallback(async () => {
    try {
      setData(await get<Unmapped>(`${BASE}/months/${month}/unmapped`));
      setKnownParties(await get<KnownParty[]>(`${BASE}/mappings/parties`));
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not load the unmapped lines.",
      );
    }
  }, [month]);

  useEffect(() => {
    void load();
  }, [load]);

  /** What the typed name still needs: nothing when it is a known, complete name. */
  function needsFor(key: string): { location: boolean; cf: boolean } {
    const typed = (forms[key]?.party ?? "").trim().toLowerCase();
    const known = typed
      ? knownParties.find((p) => p.name.trim().toLowerCase() === typed)
      : undefined;
    if (!known || known.sharing_group) return { location: !known, cf: !known };
    if (!known.location) return { location: true, cf: true };
    return { location: false, cf: !known.effective_cf_location };
  }

  function canSave(key: string): boolean {
    const form = forms[key];
    if (!form?.party.trim()) return false;
    const needs = needsFor(key);
    return !(
      (needs.location && !form.location) ||
      (needs.cf && !form.cf_location)
    );
  }

  async function save(key: string) {
    const form = forms[key];
    if (!form?.party.trim() || !canSave(key)) return;
    setSaving(key);
    setError(null);
    try {
      await post(`${BASE}/mappings/resolve-key`, {
        desc_key: key,
        party: form.party,
        location: needsFor(key).location ? form.location || null : null,
        cf_location: needsFor(key).cf ? form.cf_location || null : null,
      });
      setSaved((previous) => ({ ...previous, [key]: true }));
      onChanged();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not save the mapping.",
      );
    } finally {
      setSaving(null);
    }
  }

  if (!data)
    return error ? <ErrorBanner>{error}</ErrorBanner> : <LoadingNotice />;
  const remaining = rows.filter((row) => !saved[row.desc_key]).length;

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4">
      <div className="flex items-center gap-2 text-amber-600">
        <AlertTriangle className="h-4 w-4" />
        <h4 className="font-display text-sm font-semibold">
          Fix mappings for {monthLabel(month)}
        </h4>
      </div>
      <p className="text-sm text-ink-dim">
        Choose the name for each bank description. A new name needs both its
        location and CF location; a name already in the list needs neither. The
        mapping is remembered for every future statement. The month is generated
        once all lines are mapped, or use Download anyway.
      </p>
      {error && (
        <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>
      )}

      {rows.length > 0 && (
        <div className="flex flex-col gap-2">
          {pagination.pagedItems.map((row) => (
            <div
              key={row.desc_key}
              className="flex flex-col gap-2 rounded-lg border border-amber-500/20 bg-bg-soft/40 px-3 py-2.5"
            >
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-sm font-medium text-ink">
                  {row.sample}
                </span>
                <span className="text-xs text-ink-faint">
                  {row.direction === "D" ? "Payment" : "Receipt"} ·{" "}
                  {formatIndianNumber(row.count)} line
                  {row.count === 1 ? "" : "s"} · ₹{money(row.total)}
                </span>
              </div>
              {saved[row.desc_key] ? (
                <span className="inline-flex items-center gap-1 text-sm text-emerald-500">
                  <CheckCircle2 className="h-4 w-4" /> Saved
                </span>
              ) : (
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <CreatableCombobox
                    placeholder="Name"
                    ariaLabel={`Name for ${row.sample}`}
                    value={forms[row.desc_key]?.party ?? ""}
                    options={options.parties}
                    suggestionLabel="Existing names"
                    onChange={(party) =>
                      setForms((previous) => ({
                        ...previous,
                        [row.desc_key]: {
                          party,
                          location: previous[row.desc_key]?.location ?? "",
                          cf_location:
                            previous[row.desc_key]?.cf_location ?? "",
                        },
                      }))
                    }
                    className="w-full sm:w-72"
                  />
                  {needsFor(row.desc_key).location && (
                    <CreatableCombobox
                      placeholder="Location"
                      ariaLabel={`Location for ${row.sample}`}
                      value={forms[row.desc_key]?.location ?? ""}
                      options={options.locations}
                      suggestionLabel="Existing locations"
                      onChange={(location) =>
                        setForms((previous) => ({
                          ...previous,
                          [row.desc_key]: {
                            party: previous[row.desc_key]?.party ?? "",
                            location,
                            cf_location:
                              previous[row.desc_key]?.cf_location ?? "",
                          },
                        }))
                      }
                      className="w-full sm:w-64"
                    />
                  )}
                  {needsFor(row.desc_key).cf && (
                    <CreatableCombobox
                      placeholder="CF location"
                      ariaLabel={`CF location for ${row.sample}`}
                      value={forms[row.desc_key]?.cf_location ?? ""}
                      options={options.cf_locations}
                      suggestionLabel="CF locations"
                      onChange={(cf_location) =>
                        setForms((previous) => ({
                          ...previous,
                          [row.desc_key]: {
                            party: previous[row.desc_key]?.party ?? "",
                            location: previous[row.desc_key]?.location ?? "",
                            cf_location,
                          },
                        }))
                      }
                      className="w-full sm:w-64"
                    />
                  )}
                  <Button
                    variant="secondary"
                    loading={saving === row.desc_key}
                    disabled={!canSave(row.desc_key)}
                    onClick={() => void save(row.desc_key)}
                  >
                    Save
                  </Button>
                </div>
              )}
            </div>
          ))}
          <Pagination
            page={pagination.page}
            pageCount={pagination.pageCount}
            pageSize={pagination.pageSize}
            totalItems={pagination.totalItems}
            itemLabel="unmapped descriptions"
            onPageChange={pagination.setPage}
            onPageSizeChange={pagination.setPageSize}
          />
        </div>
      )}

      {(data.other ?? []).length > 0 && (
        <div className="subpanel flex flex-col gap-3 p-3">
          <span className="text-xs font-bold tracking-[0.08em] text-amber-600 uppercase">
            Other things to fix
          </span>
          {data.other.map((item) => (
            <OtherIssue
              key={`${item.issue}-${item.name}-${item.sample}-${item.source}`}
              item={item}
              options={options}
              onFixed={() => {
                void load();
                onChanged();
              }}
            />
          ))}
        </div>
      )}

      <p className="text-xs text-ink-faint">
        Not everything is known yet? <strong>Download anyway</strong> builds the
        workbook now. Lines that are still unmapped are highlighted with a
        Remark on the Payments / Receipts sheets and are left out of the Summary
        figures until they are mapped. Map them later and regenerate.
      </p>

      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          variant="secondary"
          icon={<ListChecks className="h-4 w-4" />}
          onClick={() => onReview(month)}
        >
          Review lines
        </Button>
        <Button
          variant="secondary"
          icon={<Download className="h-4 w-4" />}
          loading={generating}
          onClick={() => onDownloadAnyway(month)}
        >
          Download anyway
        </Button>
        <Button
          icon={<RefreshCw className="h-4 w-4" />}
          loading={generating}
          disabled={remaining > 0 && rows.length > 0}
          onClick={() => onGenerate(month)}
        >
          Generate {monthLabel(month)}
        </Button>
      </div>
    </div>
  );
}

// ── panel ────────────────────────────────────────────────────────────────

export function UploadPanel({
  months,
  monthsLoading,
  options,
  reloadMonths,
  reloadOptions,
  onReview,
  onSetup,
}: {
  months: MonthRow[];
  monthsLoading: boolean;
  options: MappingOptions;
  reloadMonths: () => Promise<void>;
  reloadOptions: () => Promise<void>;
  onReview: (month: string) => void;
  onSetup: (month: string) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploadJob, setUploadJob] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [genJob, setGenJob] = useState<{ month: string; id: string } | null>(
    null,
  );
  const [genOutcome, setGenOutcome] = useState<GenerateOutcome | null>(null);
  const [fixMonth, setFixMonth] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const autoDownload = useRef<string | null>(null); // month to save as soon as "Download anyway" finishes
  const pagination = usePagination(months, 12);

  async function upload() {
    if (files.length === 0) return;
    setSubmitting(true);
    setError(null);
    setUploadResult(null);
    try {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      const res = await postForm<{ job_id: string }>(
        `${BASE}/statements`,
        body,
      );
      setUploadJob(res.job_id);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not upload the statements.",
      );
      setSubmitting(false);
    }
  }

  async function generate(month: string, allowUnmapped = false) {
    setError(null);
    setGenOutcome(null);
    autoDownload.current = allowUnmapped ? month : null;
    try {
      const res = await post<{ job_id: string }>(
        `${BASE}/months/${month}/generate${allowUnmapped ? "?allow_unmapped=true" : ""}`,
      );
      setGenJob({ month, id: res.job_id });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not start generation.",
      );
    }
  }

  function download(month: string) {
    const a = document.createElement("a");
    a.href = apiUrl(`${BASE}/months/${month}/download`);
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  const needsFix = (uploadResult?.months ?? []).filter(
    (m) => m.status === "needs_mapping_fix",
  );

  return (
    <div className="flex flex-col gap-6">
      <GlassCard padding="lg" className="flex flex-col gap-5">
        <div className="flex items-center gap-4">
          <span className="icon-tile grid h-12 w-12 place-items-center rounded-xl">
            <UploadCloud className="h-5 w-5" />
          </span>
          <div>
            <h2 className="font-display text-xl font-semibold tracking-[-0.025em] text-ink">
              Upload bank statements
            </h2>
            <p className="mt-1 text-sm leading-6 text-ink-dim">
              Upload one or more statements (Excel of any kind - .xlsx, .xlsb, .xls - or .csv). Lines already
              stored are skipped, so overlapping statements are safe. Every
              month that receives new lines is regenerated once all of its lines
              are mapped.
            </p>
          </div>
        </div>

        <FileDropzone
          multiple
          accept={EXCEL_ACCEPT}
          label="Drag & drop bank statements here, or click to browse"
          files={files}
          onFilesSelected={(selected) =>
            setFiles((previous) => [...previous, ...selected])
          }
          onRemove={(index) =>
            setFiles((previous) => previous.filter((_, i) => i !== index))
          }
        />

        <div className="flex justify-stretch sm:justify-end">
          <Button
            loading={submitting}
            disabled={files.length === 0}
            onClick={() => void upload()}
          >
            Upload and update cashflows
          </Button>
        </div>

        {error && (
          <ErrorBanner className="whitespace-pre-wrap">{error}</ErrorBanner>
        )}

        {uploadJob && (
          <ProgressPanel<UploadResult>
            jobId={uploadJob}
            poller={pollJob}
            onDone={(result) => {
              setUploadResult(result ?? null);
              setSubmitting(false);
              setFiles([]);
              setUploadJob(null);
              void reloadMonths();
              void reloadOptions();
              const firstFix = result?.months.find(
                (m) => m.status === "needs_mapping_fix",
              );
              if (firstFix) setFixMonth(firstFix.month);
            }}
            onError={(message) => {
              setError(message);
              setSubmitting(false);
              setUploadJob(null);
            }}
            onCancel={() => post(`${BASE}/jobs/${uploadJob}/cancel`)}
          />
        )}

        {uploadResult && (
          <div className="subpanel flex flex-col gap-3 p-4">
            <h3 className="font-display text-sm font-semibold text-ink">
              Upload result
            </h3>
            {uploadResult.files.map((file) => (
              <div key={file.file} className="flex flex-col gap-1 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <FileSpreadsheet className="h-4 w-4 text-ink-faint" />
                  <span className="font-medium text-ink">{file.file}</span>
                  {file.ok ? (
                    <StatusChip tone="ok">
                      {formatIndianNumber(file.new_count ?? 0)} new ·{" "}
                      {formatIndianNumber(file.duplicate_count ?? 0)} already
                      stored · account {file.account_no}
                    </StatusChip>
                  ) : (
                    <StatusChip tone="warn">Not stored</StatusChip>
                  )}
                </div>
                {file.error && <ErrorBanner>{file.error}</ErrorBanner>}
                {(file.header_warnings ?? []).map((warning) => (
                  <p
                    key={warning}
                    className="status-banner border-amber-500/30 bg-amber-500/10 text-amber-600"
                  >
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{" "}
                    {warning} The statement may be incomplete — compare it with
                    the bank's copy.
                  </p>
                ))}
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              {uploadResult.months.map((m) => (
                <StatusChip
                  key={m.month}
                  tone={
                    m.status === "generated"
                      ? "ok"
                      : m.status === "needs_mapping_fix"
                        ? "warn"
                        : "muted"
                  }
                >
                  {monthLabel(m.month)}:{" "}
                  {m.status === "generated"
                    ? `cashflow v${m.version} saved`
                    : m.status === "needs_mapping_fix"
                      ? `${formatIndianNumber(m.issue_count ?? 0)} lines need mapping`
                      : m.status === "unchanged"
                        ? "nothing new"
                        : m.status}
                </StatusChip>
              ))}
            </div>
            {needsFix.length > 0 && (
              <p className="text-sm text-ink-dim">
                Fix the highlighted months below, then generate them.
              </p>
            )}
          </div>
        )}
      </GlassCard>

      <GlassCard padding="lg" className="flex flex-col gap-4">
        <div>
          <h2 className="font-display text-lg font-semibold text-ink">
            Monthly cashflows
          </h2>
          <p className="text-sm text-ink-dim">
            The latest generated workbook of every month is stored here. Amounts
            are in ₹.
          </p>
        </div>

        {genJob && (
          <ProgressPanel<GenerateOutcome>
            jobId={genJob.id}
            poller={pollJob}
            onDone={(result) => {
              setGenOutcome(result ?? null);
              setGenJob(null);
              void reloadMonths();
              if (result?.status === "needs_mapping_fix")
                setFixMonth(result.month);
              if (
                result?.status === "generated" &&
                autoDownload.current === result.month
              )
                download(result.month);
              autoDownload.current = null;
            }}
            onError={(message) => {
              setError(message);
              setGenJob(null);
            }}
            onCancel={() => post(`${BASE}/jobs/${genJob.id}/cancel`)}
          />
        )}
        {genOutcome?.status === "generated" && (
          <p className="status-banner border-emerald-500/30 bg-emerald-500/10 text-emerald-600">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />{" "}
            {monthLabel(genOutcome.month)} cashflow saved (version{" "}
            {genOutcome.version}).
            {(genOutcome.unmapped_count ?? 0) > 0 &&
              ` ${genOutcome.unmapped_count} unmapped line${genOutcome.unmapped_count === 1 ? " is" : "s are"} highlighted in the workbook (Remark column).`}
            {(genOutcome.earlier_issue_count ?? 0) > 0 &&
              ` Note: ${genOutcome.earlier_issue_count} unmapped lines in earlier months affect the carried-forward opening balance.`}
          </p>
        )}

        {monthsLoading ? (
          <LoadingNotice />
        ) : months.length === 0 ? (
          <p className="py-6 text-center text-sm text-ink-faint">
            No statements uploaded yet. Upload one above to get started.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-bg-soft/60 text-ink-dim">
                <tr>
                  <th className="px-4 py-3 font-medium">Month</th>
                  <th className="px-4 py-3 text-right font-medium">Lines</th>
                  <th className="px-4 py-3 text-right font-medium">Payments</th>
                  <th className="px-4 py-3 text-right font-medium">Receipts</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {pagination.pagedItems.map((row) => (
                  <tr key={row.month} className="border-t border-border/80">
                    <td className="px-4 py-3 font-medium text-ink">
                      {monthLabel(row.month)}
                    </td>
                    <td className="px-4 py-3 text-right text-ink">
                      {formatIndianNumber(row.transactions)}
                    </td>
                    <td className="px-4 py-3 text-right text-ink">
                      {money(row.payments)}
                    </td>
                    <td className="px-4 py-3 text-right text-ink">
                      {money(row.receipts)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        {row.issue_count > 0 ? (
                          <StatusChip tone="warn">
                            <AlertTriangle className="h-3.5 w-3.5" />{" "}
                            {formatIndianNumber(row.issue_count)} to fix
                          </StatusChip>
                        ) : row.file ? (
                          <StatusChip tone="ok">
                            <CheckCircle2 className="h-3.5 w-3.5" />{" "}
                            {row.file.source === "uploaded"
                              ? "Uploaded workbook"
                              : `v${row.file.version}`}{" "}
                            ·{" "}
                            {formatIndianDateTime(row.file.generated_at + "Z")}
                          </StatusChip>
                        ) : (
                          <StatusChip tone="muted">
                            Not generated yet
                          </StatusChip>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        {row.issue_count > 0 && (
                          <Button
                            variant="secondary"
                            className="min-h-9 px-3 py-1.5"
                            onClick={() => setFixMonth(row.month)}
                          >
                            Fix mappings
                          </Button>
                        )}
                        {row.transactions > 0 ? (
                          // One button: always rebuilds the workbook from the current data, then downloads it.
                          <Button
                            variant="secondary"
                            className="min-h-9 px-3 py-1.5"
                            icon={<Download className="h-4 w-4" />}
                            disabled={Boolean(genJob)}
                            onClick={() => void generate(row.month, true)}
                          >
                            {row.issue_count > 0 ? "Download anyway" : "Download"}
                          </Button>
                        ) : (
                          row.file?.available && (
                            <Button
                              variant="secondary"
                              className="min-h-9 px-3 py-1.5"
                              icon={<Download className="h-4 w-4" />}
                              onClick={() => download(row.month)}
                            >
                              Download
                            </Button>
                          )
                        )}
                        {row.transactions > 0 && (
                          <Button
                            variant="ghost"
                            className="min-h-9 px-3 py-1.5"
                            icon={<RefreshCw className="h-4 w-4" />}
                            disabled={Boolean(genJob)}
                            onClick={() => void generate(row.month)}
                          >
                            {row.file ? "Regenerate" : "Generate"}
                          </Button>
                        )}
                        {row.transactions > 0 && (
                          <Button
                            variant="ghost"
                            className="min-h-9 px-3 py-1.5"
                            icon={<ListChecks className="h-4 w-4" />}
                            onClick={() => onReview(row.month)}
                          >
                            Review
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          className="min-h-9 px-3 py-1.5"
                          icon={<Settings2 className="h-4 w-4" />}
                          onClick={() => onSetup(row.month)}
                        >
                          Setup
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination
          page={pagination.page}
          pageCount={pagination.pageCount}
          pageSize={pagination.pageSize}
          totalItems={pagination.totalItems}
          itemLabel="months"
          onPageChange={pagination.setPage}
          onPageSizeChange={pagination.setPageSize}
        />

        {fixMonth && (
          <FixPanel
            key={fixMonth}
            month={fixMonth}
            options={options}
            generating={Boolean(genJob)}
            onChanged={() => {
              void reloadOptions();
              void reloadMonths();
            }}
            onGenerate={(month) => void generate(month)}
            onDownloadAnyway={(month) => void generate(month, true)}
            onReview={onReview}
          />
        )}
      </GlassCard>
    </div>
  );
}
