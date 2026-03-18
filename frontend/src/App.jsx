import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ClipboardCheck,
  Clock,
  Download,
  FileText,
  LayoutDashboard,
  Layers,
  Mic,
  Trash2,
  User,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const formatDisplayValue = (value, fallback = "待定") => {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized || normalized === "tbd" || normalized === "unknown") return fallback;
  if (normalized === "none") return "无";
  return value;
};

const downloadBlob = (blob, filename) => {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
};

const buildFileName = (title, suffix) => {
  const safeTitle = String(title || "会议纪要").replace(/[\\/:*?"<>|]/g, "_").trim();
  return `${safeTitle || "会议纪要"}_${suffix}`;
};

const extractDownloadFilename = (response, fallback) => {
  const disposition = response.headers.get("content-disposition") || "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  if (asciiMatch?.[1]) return asciiMatch[1];
  return fallback;
};

const readErrorMessage = async (response, fallback) => {
  const extractMessage = (payload) => {
    if (!payload) return "";
    if (typeof payload === "string") return payload.trim();
    if (typeof payload?.detail === "string" && payload.detail.trim()) return payload.detail.trim();
    if (typeof payload?.message === "string" && payload.message.trim()) return payload.message.trim();
    return "";
  };

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const payload = await response.json();
    const message = extractMessage(payload);
    if (message) return message;
  }

  const text = await response.text();
  if (!text) return fallback;

  try {
    const payload = JSON.parse(text);
    const message = extractMessage(payload);
    if (message) return message;
  } catch {
    return text;
  }

  return text || fallback;
};

const splitTranscriptParagraphs = (text) => {
  const blocks = String(text || "")
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean);

  if (blocks.length > 0) return blocks;

  return String(text || "")
    .split(/(?<=[。！？!?])/)
    .map((item) => item.trim())
    .filter(Boolean);
};

const speakerPalette = [
  {
    badge: "bg-blue-100 text-blue-700",
    card: "border-blue-100 bg-blue-50/30",
  },
  {
    badge: "bg-emerald-100 text-emerald-700",
    card: "border-emerald-100 bg-emerald-50/30",
  },
  {
    badge: "bg-amber-100 text-amber-700",
    card: "border-amber-100 bg-amber-50/30",
  },
  {
    badge: "bg-rose-100 text-rose-700",
    card: "border-rose-100 bg-rose-50/30",
  },
];

const normalizeSpeakerLabel = (speaker) => {
  const raw = String(speaker || "").trim();
  if (!raw) return "发言人 1";

  const match = raw.match(/speaker\s*(\d+)/i);
  if (match) return `发言人 ${match[1]}`;

  return raw;
};

const parseSpeakerTranscript = (text) => {
  const lines = String(text || "")
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean);

  const turns = [];
  const speakerRegex = /^\[(.+?)\]:\s*(.*)$/;

  for (const line of lines) {
    const matched = line.match(speakerRegex);
    if (matched) {
      turns.push({
        speaker: normalizeSpeakerLabel(matched[1]),
        text: matched[2].trim(),
      });
      continue;
    }

    if (turns.length > 0) {
      turns[turns.length - 1].text = `${turns[turns.length - 1].text}\n${line}`.trim();
    }
  }

  if (turns.length > 0) {
    return turns.map((turn, index) => ({ ...turn, index }));
  }

  return splitTranscriptParagraphs(text).map((paragraph, index) => ({
    speaker: "发言人 1",
    text: paragraph,
    index,
  }));
};

const collectSpeakerProfiles = (turns) => {
  const profiles = [];
  const seen = new Set();

  turns.forEach((turn) => {
    if (seen.has(turn.speaker)) return;
    seen.add(turn.speaker);
    profiles.push({
      speaker: turn.speaker,
      sample: turn.text,
    });
  });

  return profiles;
};

const buildTranscriptText = (turns) =>
  turns
    .map((turn) => `[${turn.speaker}]: ${String(turn.text || "").trim()}`.trim())
    .filter(Boolean)
    .join("\n");

const buildTranscriptMarkdown = (title, transcriptText) =>
  `# ${title}\n\n## \u8f6c\u5199\u5168\u6587\n\n${String(transcriptText || "").trim()}\n`;

const getSpeakerBadgeText = (speaker) => {
  const raw = String(speaker || "").trim();
  if (!raw) return "1";

  const digitMatch = raw.match(/(\d+)$/);
  if (digitMatch?.[1]) return digitMatch[1];

  if (raw.length <= 2) return raw;
  return raw.slice(-2);
};

const formatHistoryDate = (date) => String(date || "未标注日期").trim() || "未标注日期";
const formatHistoryTime = (value) => {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 19);
};
const getReportTypeLabel = (reportType) => (reportType === "management" ? "管理版" : "项目版");
const getHistoryTypeLabel = (item) => (item.entry_type === "audio_transcript" ? "语音转写" : "会议解析");
const getHistoryDownloadOptions = (item) => {
  if (item.entry_type === "audio_transcript") {
    return [
      { key: "transcript-markdown", label: "语音转文本 Markdown" },
      { key: "transcript-docx", label: "语音转文本 Word" },
    ];
  }
  return [
    { key: "management-markdown", label: "管理版 Markdown" },
    { key: "management-docx", label: "管理版 Word" },
    { key: "project-markdown", label: "项目版 Markdown" },
    { key: "project-docx", label: "项目版 Word" },
  ];
};
const getHistoryFallbackSuffix = (downloadOption) => {
  switch (downloadOption) {
    case "management-markdown":
      return "管理版.md";
    case "management-docx":
      return "管理版.docx";
    case "project-markdown":
      return "项目版.md";
    case "project-docx":
      return "项目版.docx";
    case "transcript-markdown":
      return "语音转文本.md";
    case "transcript-docx":
      return "语音转文本.docx";
    default:
      return "报告.md";
  }
};

const App = () => {
  const fileInputRef = useRef(null);
  const audioInputRef = useRef(null);
  const [activeTab, setActiveTab] = useState("dashboard");
  const [inputText, setInputText] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [reportType, setReportType] = useState("management");
  const [extractedData, setExtractedData] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState("");
  const [actionItems, setActionItems] = useState([]);
  const [isDownloading, setIsDownloading] = useState(false);
  const [voiceFile, setVoiceFile] = useState(null);
  const [voiceTitle, setVoiceTitle] = useState("");
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voiceMarkdown, setVoiceMarkdown] = useState("");
  const [voiceError, setVoiceError] = useState("");
  const [voiceView, setVoiceView] = useState("transcript");
  const [isTranscribingVoice, setIsTranscribingVoice] = useState(false);
  const [isDownloadingVoice, setIsDownloadingVoice] = useState(false);
  const [pendingVoiceResult, setPendingVoiceResult] = useState(null);
  const [speakerNameDrafts, setSpeakerNameDrafts] = useState({});
  const [isSpeakerBindingOpen, setIsSpeakerBindingOpen] = useState(false);
  const [isSavingSpeakerNames, setIsSavingSpeakerNames] = useState(false);
  const [historyItems, setHistoryItems] = useState([]);
  const [historyError, setHistoryError] = useState("");
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [historyMenuId, setHistoryMenuId] = useState(null);
  const [historyDownloadingKey, setHistoryDownloadingKey] = useState("");
  const [historyDeletingKey, setHistoryDeletingKey] = useState("");
  const [isClearingHistory, setIsClearingHistory] = useState(false);

  const normalizeActionItems = (actions = []) =>
    actions.map((action, index) => ({
      id: index + 1,
      task: action.task,
      owner: formatDisplayValue(action.owner),
      deadline: formatDisplayValue(action.deadline),
      status: "pending",
      priority: formatDisplayValue(action.risk, "无") !== "无" ? "high" : "medium",
    }));

  const fetchHistory = useCallback(async () => {
    setIsHistoryLoading(true);
    setHistoryError("");
    try {
      const response = await fetch(`${API_BASE}/api/history`);
      if (!response.ok) {
        const message = await readErrorMessage(response, "加载历史记录失败");
        throw new Error(message || "加载历史记录失败");
      }

      const payload = await response.json();
      setHistoryItems(Array.isArray(payload) ? payload : []);
    } catch (requestError) {
      setHistoryError(requestError.message || "加载历史记录失败，请稍后重试");
    } finally {
      setIsHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchHistory();
  }, [fetchHistory]);

  const handleFileButtonClick = () => fileInputRef.current?.click();
  const handleAudioFileButtonClick = () => audioInputRef.current?.click();

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0];
    setError("");
    setSelectedFile(file || null);
    if (!file) return;

    const ext = file.name.toLowerCase().split(".").pop();
    if (ext === "txt" || ext === "md") {
      const text = await file.text();
      setInputText(text);
    }
  };

  const handleAudioFileChange = (event) => {
    const file = event.target.files?.[0];
    setVoiceError("");
    setVoiceFile(file || null);
    setPendingVoiceResult(null);
    setSpeakerNameDrafts({});
    setIsSpeakerBindingOpen(false);
    if (!file) return;

    setVoiceTitle(file.name.replace(/\.[^.]+$/, ""));
    setVoiceTranscript("");
    setVoiceMarkdown("");
    setVoiceView("transcript");
  };

  const handleAnalyze = async () => {
    setIsAnalyzing(true);
    setError("");
    try {
      let response;
      if (selectedFile) {
        const formData = new FormData();
        formData.append("file", selectedFile);
        response = await fetch(`${API_BASE}/api/analyze/upload?report_type=${encodeURIComponent(reportType)}`, {
          method: "POST",
          body: formData,
        });
      } else {
        response = await fetch(`${API_BASE}/api/analyze/text`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ text: inputText, report_type: reportType }),
        });
      }

      if (!response.ok) {
        const message = await readErrorMessage(response, "解析失败");
        throw new Error(message || "解析失败");
      }

      const payload = await response.json();
      setExtractedData(payload);
      setActionItems((prev) => [...normalizeActionItems(payload.actions), ...prev].slice(0, 12));
      setHistoryMenuId(null);
      void fetchHistory();
    } catch (requestError) {
      setError(requestError.message || "服务异常，请稍后重试");
    } finally {
      setIsAnalyzing(false);
    }
  };

  const buildReportPayload = () => {
    if (!extractedData) return null;
    return {
      title: extractedData.title,
      date: extractedData.date,
      weekly_period: extractedData.weekly_period,
      decisions: extractedData.decisions || [],
      actions: (extractedData.actions || []).map((action) => ({
        task: action.task,
        owner: formatDisplayValue(action.owner),
        deadline: formatDisplayValue(action.deadline),
        risk: formatDisplayValue(action.risk, "无"),
      })),
      risks: extractedData.risks || [],
      report_type: reportType,
    };
  };

  const handleDownload = async (type) => {
    const payload = buildReportPayload();
    if (!payload) {
      setError("当前没有可导出的解析结果。");
      return;
    }

    setIsDownloading(true);
    setError("");

    try {
      const endpoint = type === "docx" ? "/api/report/weekly/docx" : "/api/report/weekly/markdown";
      const response = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, "导出失败");
        throw new Error(message || "导出失败");
      }

      const blob = await response.blob();
      const fallback = buildFileName(payload.title, type === "docx" ? `${reportType}.docx` : `${reportType}.md`);
      const filename = extractDownloadFilename(response, fallback);
      downloadBlob(blob, filename);
    } catch (requestError) {
      setError(requestError.message || "导出失败，请稍后重试");
    } finally {
      setIsDownloading(false);
    }
  };
  const buildVoicePayload = () => {
    if (!voiceTranscript.trim()) return null;
    return {
      title: voiceTitle || voiceFile?.name?.replace(/\.[^.]+$/, "") || "会议语音转写",
      transcript_text: voiceTranscript,
    };
  };

  const handleVoiceTranscribe = async () => {
    if (!voiceFile) {
      setVoiceError("请先选择音频文件。");
      return;
    }

    setIsTranscribingVoice(true);
    setVoiceError("");

    try {
      const formData = new FormData();
      formData.append("file", voiceFile);

      const response = await fetch(`${API_BASE}/api/audio/transcribe`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, "语音转写失败");
        throw new Error(message || "语音转写失败");
      }

      const payload = await response.json();
      const transcriptTitle = payload.title || voiceFile.name.replace(/\.[^.]+$/, "");
      const transcriptText = payload.transcript_text || "";
      const speakerProfiles = collectSpeakerProfiles(parseSpeakerTranscript(transcriptText));

      setVoiceTitle(transcriptTitle);
      setVoiceTranscript("");
      setVoiceMarkdown("");
      setVoiceView("transcript");

      if (speakerProfiles.length === 0) {
        setPendingVoiceResult(null);
        setSpeakerNameDrafts({});
        setIsSpeakerBindingOpen(false);
        setVoiceTranscript(transcriptText);
        setVoiceMarkdown(buildTranscriptMarkdown(transcriptTitle, transcriptText));
        await fetchHistory();
      } else {
        setPendingVoiceResult({
          title: transcriptTitle,
          transcriptText,
          historyId: payload.history_id ?? null,
        });
        setSpeakerNameDrafts(
          Object.fromEntries(speakerProfiles.map((profile) => [profile.speaker, ""]))
        );
        setIsSpeakerBindingOpen(true);
      }
    } catch (requestError) {
      setVoiceError(requestError.message || "语音转写失败，请稍后重试");
    } finally {
      setIsTranscribingVoice(false);
    }
  };

  const handleVoiceDownload = async (type) => {
    const payload = buildVoicePayload();
    if (!payload) {
      setVoiceError("当前没有可导出的转写内容。");
      return;
    }

    setIsDownloadingVoice(true);
    setVoiceError("");

    try {
      const endpoint = type === "docx" ? "/api/audio/transcribe/docx" : "/api/audio/transcribe/markdown";
      const response = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const message = await readErrorMessage(response, "下载失败");
        throw new Error(message || "下载失败");
      }

      const blob = await response.blob();
      const fallback = buildFileName(payload.title, type === "docx" ? "转写稿.docx" : "转写稿.md");
      const filename = extractDownloadFilename(response, fallback);
      downloadBlob(blob, filename);
    } catch (requestError) {
      setVoiceError(requestError.message || "下载失败，请稍后重试");
    } finally {
      setIsDownloadingVoice(false);
    }
  };

  const handleSpeakerNameDraftChange = (speaker, value) => {
    setSpeakerNameDrafts((current) => ({
      ...current,
      [speaker]: value,
    }));
  };

  const handleConfirmSpeakerNames = async () => {
    if (!pendingVoiceResult) return;

    const { historyId, title, transcriptText } = pendingVoiceResult;
    const namedTurns = parseSpeakerTranscript(transcriptText).map((turn) => ({
      ...turn,
      speaker: String(speakerNameDrafts[turn.speaker] || turn.speaker).trim() || turn.speaker,
    }));
    const namedTranscript = buildTranscriptText(namedTurns);
    const namedMarkdown = buildTranscriptMarkdown(title, namedTranscript);

    setVoiceError("");
    setVoiceTitle(title);
    setVoiceTranscript(namedTranscript);
    setVoiceMarkdown(namedMarkdown);
    setVoiceView("transcript");
    setIsSpeakerBindingOpen(false);
    setPendingVoiceResult(null);
    setSpeakerNameDrafts({});

    if (historyId) {
      setIsSavingSpeakerNames(true);
      try {
        const response = await fetch(`${API_BASE}/api/history/${historyId}/audio-transcript`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            title,
            transcript_text: namedTranscript,
          }),
        });
        if (!response.ok) {
          const message = await readErrorMessage(response, "同步实名转写失败");
          throw new Error(message || "同步实名转写失败");
        }
      } catch (requestError) {
        setVoiceError(
          requestError.message || "已完成姓名绑定，但历史记录同步失败，请稍后重试"
        );
      } finally {
        setIsSavingSpeakerNames(false);
      }
    }

    await fetchHistory();
  };

  const handleHistoryDownload = async (item, downloadOption) => {
    const downloadKey = `${item.id}-${downloadOption}`;
    setHistoryDownloadingKey(downloadKey);
    setHistoryError("");

    try {
      const response = await fetch(`${API_BASE}/api/history/${item.id}/download/${downloadOption}`);
      if (!response.ok) {
        const message = await readErrorMessage(response, "下载历史报告失败");
        throw new Error(message || "下载历史报告失败");
      }

      const blob = await response.blob();
      const fallback = buildFileName(item.title, getHistoryFallbackSuffix(downloadOption));
      const filename = extractDownloadFilename(response, fallback);
      downloadBlob(blob, filename);
      setHistoryMenuId(null);
    } catch (requestError) {
      setHistoryError(requestError.message || "下载历史报告失败，请稍后重试");
    } finally {
      setHistoryDownloadingKey("");
    }
  };

  const handleDeleteHistory = async (item) => {
    const confirmed = window.confirm(`确认删除历史记录“${item.title}”吗？`);
    if (!confirmed) return;

    setHistoryDeletingKey(String(item.id));
    setHistoryError("");

    try {
      const response = await fetch(`${API_BASE}/api/history/${item.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const message = await readErrorMessage(response, "删除历史记录失败");
        throw new Error(message || "删除历史记录失败");
      }

      setHistoryMenuId((current) => (current === item.id ? null : current));
      await fetchHistory();
    } catch (requestError) {
      setHistoryError(requestError.message || "删除历史记录失败，请稍后重试");
    } finally {
      setHistoryDeletingKey("");
    }
  };

  const handleClearHistory = async () => {
    if (historyItems.length === 0) return;

    const confirmed = window.confirm("确认清空全部历史记录吗？此操作不可恢复。");
    if (!confirmed) return;

    setIsClearingHistory(true);
    setHistoryError("");

    try {
      const response = await fetch(`${API_BASE}/api/history`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const message = await readErrorMessage(response, "清空历史记录失败");
        throw new Error(message || "清空历史记录失败");
      }

      setHistoryMenuId(null);
      await fetchHistory();
    } catch (requestError) {
      setHistoryError(requestError.message || "清空历史记录失败，请稍后重试");
    } finally {
      setIsClearingHistory(false);
    }
  };

  const speakerTurns = parseSpeakerTranscript(voiceTranscript);
  const pendingSpeakerProfiles = pendingVoiceResult
    ? collectSpeakerProfiles(parseSpeakerTranscript(pendingVoiceResult.transcriptText))
    : [];
  const canConfirmSpeakerNames =
    pendingSpeakerProfiles.length > 0 &&
    pendingSpeakerProfiles.every((profile) => String(speakerNameDrafts[profile.speaker] || "").trim());

  const renderAnalysisPreview = () => {
    if (!extractedData) {
      return (
        <div className="h-full min-h-[360px] flex flex-col items-center justify-center text-center text-slate-400">
          <FileText size={42} className="mb-4 text-slate-300" />
          <p className="text-lg font-semibold text-slate-500">完成解析后，这里会直接显示纪要预览</p>
          <p className="text-sm mt-2">上方粘贴会议内容或上传文档后，点击“开始解析”即可生成结构化结果。</p>
        </div>
      );
    }

    return (
      <div className="space-y-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 className="text-xl font-bold text-slate-900">解析结果预览</h3>
            <p className="text-sm text-slate-500 mt-1">
              {extractedData.title || "会议纪要预览"}
              {extractedData.date ? ` · ${extractedData.date}` : ""}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex space-x-2 bg-slate-200 p-1 rounded-lg">
              <button
                onClick={() => setReportType("management")}
                className={`px-4 py-1.5 text-sm rounded-md transition-all ${
                  reportType === "management" ? "bg-white shadow-sm font-bold" : "text-slate-600"
                }`}
              >
                管理版周报
              </button>
              <button
                onClick={() => setReportType("project")}
                className={`px-4 py-1.5 text-sm rounded-md transition-all ${
                  reportType === "project" ? "bg-white shadow-sm font-bold" : "text-slate-600"
                }`}
              >
                项目版纪要
              </button>
            </div>

            <button
              onClick={() => handleDownload("docx")}
              disabled={isDownloading}
              className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
            >
              下载 Word
            </button>
            <button
              onClick={() => handleDownload("markdown")}
              disabled={isDownloading}
              className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:cursor-not-allowed disabled:opacity-60"
            >
              下载 Markdown
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2">
            <div className="bg-white p-8 rounded-2xl border border-slate-200 shadow-sm min-h-[560px]">
              {reportType === "management" ? (
                <div className="space-y-6">
                  <div className="border-b pb-4">
                    <h1 className="text-2xl font-bold text-center mb-2">项目周报</h1>
                    <p className="text-center text-slate-400 text-sm">{extractedData.weekly_period}</p>
                  </div>
                  <div>
                    <h4 className="font-bold flex items-center text-blue-700 mb-2">
                      <CheckCircle2 size={18} className="mr-2" />
                      决策项
                    </h4>
                    <ul className="list-disc list-inside space-y-1 text-slate-700 text-sm pl-2">
                      {(extractedData.decisions || []).map((decision, index) => (
                        <li key={`${decision}-${index}`}>{decision}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4 className="font-bold flex items-center text-indigo-700 mb-2">
                      <Layers size={18} className="mr-2" />
                      行动项
                    </h4>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-slate-50 text-slate-500">
                          <th className="text-left py-2 px-3 font-medium">任务</th>
                          <th className="text-left py-2 px-3 font-medium">负责人</th>
                          <th className="text-left py-2 px-3 font-medium">截止日期</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(extractedData.actions || []).map((action, index) => (
                          <tr key={`${action.task}-${index}`} className="border-b border-slate-50">
                            <td className="py-3 px-3">{action.task}</td>
                            <td className="py-3 px-3 font-medium">{formatDisplayValue(action.owner)}</td>
                            <td className="py-3 px-3 text-slate-500">{formatDisplayValue(action.deadline)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="bg-red-50 p-4 rounded-xl border border-red-100">
                    <h4 className="font-bold flex items-center text-red-700 mb-1 text-sm">
                      <AlertCircle size={16} className="mr-2" />
                      风险项
                    </h4>
                    <ul className="list-disc list-inside text-red-600 text-xs pl-2 space-y-1">
                      {(extractedData.risks || []).map((risk, index) => (
                        <li key={`${risk}-${index}`}>{risk}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="flex justify-between items-start">
                    <h1 className="text-xl font-bold text-slate-800">{extractedData.title}</h1>
                    <span className="bg-blue-100 text-blue-700 text-xs px-2 py-1 rounded">会议纪要</span>
                  </div>
                  <div className="grid grid-cols-2 gap-4 text-xs text-slate-500">
                    <div className="flex items-center space-x-2">
                      <Calendar size={14} /> <span>日期：{extractedData.date}</span>
                    </div>
                    <div className="flex items-center space-x-2">
                      <User size={14} /> <span>记录人：AI 助手</span>
                    </div>
                  </div>
                  <hr className="border-slate-100" />
                  <div>
                    <p className="text-sm font-bold mb-2">行动摘要</p>
                    <div className="space-y-4">
                      {(extractedData.actions || []).map((action, index) => (
                        <div key={`${action.task}-${index}`} className="flex space-x-3 items-start border-l-2 border-blue-500 pl-4 py-1">
                          <div>
                            <p className="text-sm font-medium">{action.task}</p>
                            <div className="flex space-x-4 mt-1 text-xs text-slate-400">
                              <span>{formatDisplayValue(action.owner)}</span>
                              <span>{formatDisplayValue(action.deadline)}</span>
                              {formatDisplayValue(action.risk, "无") !== "无" && (
                                <span className="text-orange-500">{formatDisplayValue(action.risk, "无")}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="space-y-4">
            <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
              <h4 className="font-bold mb-4 flex items-center text-sm">
                <Download size={16} className="mr-2 text-blue-600" />
                提取结果
              </h4>
              <div className="space-y-3 text-sm text-slate-600">
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3">
                  <span>决策项</span>
                  <span className="font-semibold text-slate-900">{(extractedData.decisions || []).length}</span>
                </div>
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3">
                  <span>行动项</span>
                  <span className="font-semibold text-slate-900">{(extractedData.actions || []).length}</span>
                </div>
                <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3">
                  <span>风险项</span>
                  <span className="font-semibold text-slate-900">{(extractedData.risks || []).length}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const SidebarItem = ({ id, icon: Icon, label }) => (
    <button
      onClick={() => setActiveTab(id)}
      className={`w-full flex items-center space-x-3 px-4 py-3 rounded-lg transition-colors ${
        activeTab === id ? "bg-blue-600 text-white shadow-lg" : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      <Icon size={20} />
      <span className="font-medium">{label}</span>
    </button>
  );

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900 font-sans">
      <aside className="w-64 bg-white border-r border-slate-200 flex flex-col p-4">
        <div className="flex items-center space-x-2 px-4 mb-8">
          <div className="bg-blue-600 p-1.5 rounded-lg">
            <ClipboardCheck className="text-white" size={24} />
          </div>
          <h1 className="text-xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
            会议纪要助手
          </h1>
        </div>

        <nav className="flex-1 space-y-1">
          <SidebarItem id="dashboard" icon={LayoutDashboard} label="工作台" />
          <SidebarItem id="new" icon={FileText} label="新建纪要" />
          <SidebarItem id="voice" icon={Mic} label="会议语音" />
          <SidebarItem id="tracking" icon={Clock} label="行动追踪" />
        </nav>
      </aside>

      <main className="flex-1 overflow-y-auto p-8">
        {activeTab === "dashboard" && (
          <div className="max-w-5xl mx-auto animate-fade-in space-y-8">
            <div>
              <h2 className="text-2xl font-bold mb-2">欢迎回来</h2>
              <p className="text-slate-500">这里展示当前会议解析、行动项状态和历史保存记录。</p>
            </div>

            <div className="grid grid-cols-3 gap-6">
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div className="bg-orange-100 p-2 rounded-lg text-orange-600">
                    <AlertCircle size={24} />
                  </div>
                  <span className="text-xs font-bold text-slate-400">已逾期</span>
                </div>
                <h3 className="text-3xl font-bold">{actionItems.filter((x) => x.status === "overdue").length}</h3>
                <p className="text-sm text-slate-500 mt-1">需要立即跟进的行动项</p>
              </div>
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div className="bg-blue-100 p-2 rounded-lg text-blue-600">
                    <Clock size={24} />
                  </div>
                  <span className="text-xs font-bold text-slate-400">进行中</span>
                </div>
                <h3 className="text-3xl font-bold">{actionItems.filter((x) => x.status === "pending").length}</h3>
                <p className="text-sm text-slate-500 mt-1">本周待处理事项</p>
              </div>
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div className="bg-green-100 p-2 rounded-lg text-green-600">
                    <CheckCircle2 size={24} />
                  </div>
                  <span className="text-xs font-bold text-slate-400">已完成</span>
                </div>
                <h3 className="text-3xl font-bold">{actionItems.filter((x) => x.status === "completed").length}</h3>
                <p className="text-sm text-slate-500 mt-1">最近 7 天已完成事项</p>
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-visible">
              <div className="px-6 py-4 border-b border-slate-100 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h3 className="font-bold text-slate-900">历史记录</h3>
                  <p className="text-sm text-slate-500 mt-1">已保存的会议解析结果支持按 Markdown 或 Word 下载。</p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={() => void fetchHistory()}
                    disabled={isHistoryLoading || isClearingHistory}
                    className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {isHistoryLoading ? "加载中..." : "刷新列表"}
                  </button>
                  <button
                    onClick={() => void handleClearHistory()}
                    disabled={isHistoryLoading || isClearingHistory || historyItems.length === 0}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-red-50 text-sm font-medium text-red-600 hover:bg-red-100 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    <Trash2 size={16} />
                    {isClearingHistory ? "清空中..." : "清空历史"}
                  </button>
                </div>
              </div>

              {historyError && (
                <div className="mx-6 mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {historyError}
                </div>
              )}

              <div className="divide-y divide-slate-100">
                {isHistoryLoading && historyItems.length === 0 ? (
                  <div className="px-6 py-10 text-sm text-slate-400">正在加载历史记录...</div>
                ) : historyItems.length === 0 ? (
                  <div className="px-6 py-10 text-sm text-slate-400">暂无历史记录，完成一次会议解析后会自动出现在这里。</div>
                ) : (
                  historyItems.map((item) => {
                    const entryLabel = getHistoryTypeLabel(item);
                    const downloadOptions = getHistoryDownloadOptions(item);
                    const isMenuBusy = historyDownloadingKey.startsWith(`${item.id}-`);
                    const isItemDeleting = historyDeletingKey === String(item.id);
                    const isItemBusy = isMenuBusy || isItemDeleting || isClearingHistory;
                    return (
                      <div key={item.id} className="px-6 py-5 flex flex-col gap-4 md:flex-row md:items-center md:justify-between hover:bg-slate-50 transition-colors">
                        <div>
                          <p className="text-base font-semibold text-slate-900">{item.title}</p>
                          <p className="text-sm text-slate-500 mt-1">
                            {formatHistoryDate(item.date)}
                            <span className="mx-2">·</span>
                            {entryLabel}
                            {item.created_at && (
                              <>
                                <span className="mx-2">·</span>
                                保存于 {formatHistoryTime(item.created_at)}
                              </>
                            )}
                          </p>
                        </div>

                        <div className="flex flex-wrap items-center gap-3 self-start md:self-auto">
                          <div className="relative">
                            <button
                              onClick={() => setHistoryMenuId((current) => (current === item.id ? null : item.id))}
                              disabled={isItemBusy}
                              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                            >
                              <Download size={16} />
                              下载报告
                              <ChevronDown size={16} />
                            </button>

                            {historyMenuId === item.id && (
                              <div className="absolute right-0 mt-2 w-64 rounded-2xl border border-slate-200 bg-white shadow-xl p-2 z-20">
                                {downloadOptions.map((option) => {
                                  const optionKey = `${item.id}-${option.key}`;
                                  return (
                                    <button
                                      key={option.key}
                                      onClick={() => handleHistoryDownload(item, option.key)}
                                      disabled={isItemBusy}
                                      className="w-full text-left px-3 py-2 rounded-xl text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-60 disabled:cursor-not-allowed"
                                    >
                                      {historyDownloadingKey === optionKey ? "下载中..." : option.label}
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                          </div>

                          <button
                            onClick={() => void handleDeleteHistory(item)}
                            disabled={isItemBusy}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-red-50 text-sm font-medium text-red-600 hover:bg-red-100 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
                          >
                            <Trash2 size={16} />
                            {isItemDeleting ? "删除中..." : "删除"}
                          </button>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        )}
        {activeTab === "new" && (
          <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
            <div>
              <h2 className="text-2xl font-bold">生成会议纪要</h2>
              <p className="text-slate-500 mt-1">上方输入会议文本或上传文档，下方直接查看解析结果预览。</p>
            </div>

            <div className="bg-white rounded-[28px] border border-slate-200 shadow-sm p-6 md:p-8 space-y-6">
              <div className="rounded-[24px] border border-slate-200 overflow-hidden bg-white">
                <div className="px-6 py-4 border-b border-slate-100">
                  <p className="text-sm font-medium text-slate-500">会议文本输入区</p>
                </div>
                <div className="p-6 bg-slate-50">
                  <textarea
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    placeholder="请在此粘贴会议转写文本，或上传 txt / md / docx 文件。"
                    className="w-full min-h-[240px] rounded-2xl border border-slate-200 bg-white p-6 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none text-slate-700 leading-relaxed shadow-sm"
                  />
                </div>
              </div>

              <div className="rounded-[24px] border border-slate-200 bg-gradient-to-r from-slate-100 via-white to-slate-100 p-6">
                <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
                  <div className="flex items-start gap-4">
                    <div className="w-14 h-14 rounded-2xl bg-blue-100 text-blue-600 flex items-center justify-center shrink-0">
                      <FileText size={26} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">生成会议纪要</p>
                      <h3 className="text-xl font-bold text-slate-900 mt-1">上传文档或粘贴会议文本</h3>
                      <p className="text-sm text-slate-500 mt-2">支持 txt、md、docx 格式，解析完成后会在下方直接展示结构化预览。</p>
                      <p className="text-sm text-slate-700 mt-3">当前文件：{selectedFile ? selectedFile.name : "尚未选择会议文档"}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={handleFileButtonClick}
                      className="px-5 py-3 rounded-2xl bg-slate-900 text-white text-sm font-semibold hover:bg-slate-800 transition-colors"
                    >
                      添加会议文档
                    </button>
                    <button
                      onClick={handleAnalyze}
                      disabled={(!inputText && !selectedFile) || isAnalyzing}
                      className={`px-5 py-3 rounded-2xl text-sm font-semibold transition-colors ${
                        (!inputText && !selectedFile) || isAnalyzing
                          ? "bg-slate-200 text-slate-500 cursor-not-allowed"
                          : "bg-blue-600 text-white hover:bg-blue-700"
                      }`}
                    >
                      {isAnalyzing ? "正在解析..." : "开始解析"}
                    </button>
                  </div>
                </div>

                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept=".txt,.md,.docx"
                  onChange={handleFileChange}
                />
              </div>

              {!extractedData && error && (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {error}
                </div>
              )}

              <div className="rounded-[24px] border border-slate-200 overflow-hidden bg-white">
                <div className="px-6 py-4 border-b border-slate-100">
                  <p className="text-sm font-medium text-slate-500">解析结果预览</p>
                </div>
                <div className="p-6 md:p-8 min-h-[420px] bg-slate-50">{renderAnalysisPreview()}</div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "voice" && (
          <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">
            <div>
              <h2 className="text-2xl font-bold">会议语音</h2>
              <p className="text-slate-500 mt-1">上传会议音频后，系统会完成语音转写，并支持 Word 与 Markdown 下载。</p>
            </div>

            <div className="bg-white rounded-[28px] border border-slate-200 shadow-sm p-6 md:p-8 space-y-6">
              <div className="rounded-[24px] border border-slate-200 bg-gradient-to-r from-slate-100 via-white to-slate-100 p-6">
                <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-6">
                  <div className="flex items-start gap-4">
                    <div className="w-14 h-14 rounded-2xl bg-blue-100 text-blue-600 flex items-center justify-center shrink-0">
                      <Mic size={26} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">上传会议语音</p>
                      <h3 className="text-xl font-bold text-slate-900 mt-1">将语音文件转换为文本</h3>
                      <p className="text-sm text-slate-500 mt-2">支持 mp3、wav、m4a、mp4、aac、ogg、webm 格式。</p>
                      <p className="text-sm text-slate-700 mt-3">当前文件：{voiceFile ? voiceFile.name : "尚未选择音频文件"}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={handleAudioFileButtonClick}
                      className="px-5 py-3 rounded-2xl bg-slate-900 text-white text-sm font-semibold hover:bg-slate-800 transition-colors"
                    >
                      添加语音文件
                    </button>
                    <button
                      onClick={handleVoiceTranscribe}
                      disabled={!voiceFile || isTranscribingVoice}
                      className={`px-5 py-3 rounded-2xl text-sm font-semibold transition-colors ${
                        !voiceFile || isTranscribingVoice
                          ? "bg-slate-200 text-slate-500 cursor-not-allowed"
                          : "bg-blue-600 text-white hover:bg-blue-700"
                      }`}
                    >
                      {isTranscribingVoice ? "正在转写..." : "开始转写"}
                    </button>
                  </div>
                </div>

                <input
                  ref={audioInputRef}
                  type="file"
                  className="hidden"
                  accept=".mp3,.wav,.m4a,.mp4,.aac,.ogg,.webm"
                  onChange={handleAudioFileChange}
                />
              </div>

              {voiceError && (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {voiceError}
                </div>
              )}

              <div className="rounded-[24px] border border-slate-200 overflow-hidden bg-white">
                <div className="px-6 py-4 border-b border-slate-100 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                  <div className="flex items-center gap-6 text-sm font-medium">
                    <button
                      onClick={() => setVoiceView("transcript")}
                      className={`pb-2 border-b-2 transition-colors ${
                        voiceView === "transcript" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400"
                      }`}
                    >
                      转写
                    </button>
                    <button
                      onClick={() => setVoiceView("markdown")}
                      className={`pb-2 border-b-2 transition-colors ${
                        voiceView === "markdown" ? "border-slate-900 text-slate-900" : "border-transparent text-slate-400"
                      }`}
                    >
                      Markdown 预览
                    </button>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <button
                      onClick={() => handleVoiceDownload("docx")}
                      disabled={!voiceTranscript || isDownloadingVoice}
                      className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      下载 Word
                    </button>
                    <button
                      onClick={() => handleVoiceDownload("markdown")}
                      disabled={!voiceTranscript || isDownloadingVoice}
                      className="px-4 py-2 rounded-xl bg-slate-100 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      下载 Markdown
                    </button>
                  </div>
                </div>

                <div className="p-6 md:p-8 min-h-[420px] max-h-[700px] overflow-y-auto bg-slate-50">
                  {!voiceTranscript ? (
                    <div className="h-full min-h-[320px] flex flex-col items-center justify-center text-center text-slate-400">
                      <Mic size={42} className="mb-4 text-slate-300" />
                      <p className="text-lg font-semibold text-slate-500">上传音频后，这里会直接显示转写内容</p>
                      <p className="text-sm mt-2">页面上方选择音频文件后即可开始转写。</p>
                    </div>
                  ) : voiceView === "transcript" ? (
                    <div className="space-y-6">
                      {speakerTurns.map((turn) => {
                        const palette = speakerPalette[turn.index % speakerPalette.length];
                        const speakerShort = getSpeakerBadgeText(turn.speaker);

                        return (
                          <div key={`${turn.speaker}-${turn.index}`} className={`rounded-[22px] border px-6 py-5 shadow-sm ${palette.card}`}>
                            <div className="flex items-center gap-3 mb-4">
                              <span className={`inline-flex items-center justify-center min-w-12 h-9 px-3 rounded-2xl text-xs font-semibold ${palette.badge}`}>
                                {speakerShort}
                              </span>
                              <div className="flex items-center gap-3 text-sm">
                                <span className="font-semibold text-slate-800">{turn.speaker}</span>
                                <span className="text-slate-400">发言段 {String(turn.index + 1).padStart(2, "0")}</span>
                              </div>
                            </div>
                            <p className="text-[15px] leading-9 text-slate-800 whitespace-pre-wrap">{turn.text}</p>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <pre className="whitespace-pre-wrap text-sm leading-7 text-slate-700 font-mono bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                      {voiceMarkdown}
                    </pre>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
        {activeTab === "analysis" && (
          <div className="max-w-6xl mx-auto space-y-6 animate-fade-in">{renderAnalysisPreview()}</div>
        )}

        {activeTab === "tracking" && (
          <div className="max-w-5xl mx-auto animate-slide-in-from-bottom-4">
            <div className="mb-8 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold mb-1 text-slate-800">行动追踪</h2>
                <p className="text-slate-500">这里展示从会议纪要中提取出的行动项。</p>
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
              <table className="w-full text-left">
                <thead className="bg-slate-50 border-b border-slate-100">
                  <tr>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">任务</th>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">负责人</th>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">截止日期</th>
                    <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {actionItems.length === 0 ? (
                    <tr>
                      <td className="px-6 py-8 text-sm text-slate-400" colSpan={4}>
                        暂无行动项，请先完成一次会议解析。
                      </td>
                    </tr>
                  ) : (
                    actionItems.map((item) => (
                      <tr key={item.id} className="hover:bg-slate-50 transition-colors">
                        <td className="py-4 px-6 text-sm font-medium">{item.task}</td>
                        <td className="py-4 px-6 text-sm text-slate-600">{item.owner}</td>
                        <td className="py-4 px-6 text-sm text-slate-500">{item.deadline}</td>
                        <td className="py-4 px-6 text-sm text-slate-500">
                          {item.status === "completed" ? "已完成" : item.status === "overdue" ? "已逾期" : "进行中"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>

      {isSpeakerBindingOpen && pendingSpeakerProfiles.length > 0 && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-4xl rounded-[28px] border border-slate-200 bg-white shadow-2xl overflow-hidden">
            <div className="px-6 py-5 border-b border-slate-100">
              <h3 className="text-xl font-bold text-slate-900">绑定发言人姓名</h3>
              <p className="text-sm text-slate-500 mt-2">
                请先为每个 Speaker 输入真实姓名，系统会用新姓名展示转写内容和下载文档。
              </p>
            </div>

            <div className="max-h-[70vh] overflow-y-auto bg-slate-50 p-6 space-y-4">
              {pendingSpeakerProfiles.map((profile, index) => {
                const palette = speakerPalette[index % speakerPalette.length];
                const currentName = speakerNameDrafts[profile.speaker] || "";
                const sampleText =
                  profile.sample.length > 120 ? `${profile.sample.slice(0, 120)}...` : profile.sample;

                return (
                  <div key={profile.speaker} className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm">
                    <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-3">
                          <span className={`inline-flex items-center justify-center min-w-12 h-9 px-3 rounded-2xl text-xs font-semibold ${palette.badge}`}>
                            {getSpeakerBadgeText(profile.speaker)}
                          </span>
                          <div>
                            <p className="text-sm font-semibold text-slate-900">{profile.speaker}</p>
                            <p className="text-xs text-slate-500">区分依据：该 Speaker 的第一段发言</p>
                          </div>
                        </div>
                        <div className={`rounded-2xl border px-4 py-3 text-sm leading-7 text-slate-700 ${palette.card}`}>
                          {sampleText}
                        </div>
                      </div>

                      <div className="w-full lg:w-64">
                        <label className="block text-sm font-medium text-slate-700 mb-2">真实姓名</label>
                        <input
                          value={currentName}
                          onChange={(event) => handleSpeakerNameDraftChange(profile.speaker, event.target.value)}
                          placeholder="例如：张三"
                          className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="px-6 py-4 border-t border-slate-100 bg-white flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-slate-500">所有 Speaker 都需要输入姓名后才会显示转写结果。</p>
              <button
                onClick={() => void handleConfirmSpeakerNames()}
                disabled={!canConfirmSpeakerNames || isSavingSpeakerNames}
                className="px-5 py-3 rounded-2xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSavingSpeakerNames ? "正在保存..." : "确认并展示转写"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;


