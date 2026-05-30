"use client";

import { useState } from "react";
import { apiClient } from "@/lib/api";

interface LeadFormProps {
  publicId: string;
  scanToken?: string;
  title?: string;
  subtitle?: string;
  submitLabel?: string;
  fields?: string[];
}

const FIELD_CONFIG: Record<string, { label: string; type: string; placeholder: string }> = {
  name: { label: "姓名", type: "text", placeholder: "请输入姓名" },
  phone: { label: "手机号", type: "tel", placeholder: "请输入手机号" },
  region: { label: "所在地区", type: "text", placeholder: "请输入所在地区" },
  intention: { label: "意向说明", type: "text", placeholder: "请简述您的需求" },
};

const DEFAULT_FIELDS = ["name", "phone"];

export function LeadForm({
  publicId,
  scanToken,
  title,
  subtitle,
  submitLabel,
  fields: configuredFields,
}: LeadFormProps) {
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const activeFields = configuredFields?.length ? configuredFields : DEFAULT_FIELDS;

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    const fd = new FormData(e.currentTarget);
    const body: Record<string, unknown> = { public_id: publicId };
    for (const f of activeFields) {
      body[f] = fd.get(f) || "";
    }

    try {
      const res = await apiClient.post("/consumers/lead-capture", body, {
        headers: scanToken ? { Authorization: `Bearer ${scanToken}` } : {},
      });
      if (res.data?.consumer_id && typeof window !== "undefined") {
        localStorage.setItem("consumer_id", res.data.consumer_id);
      }
      setSubmitted(true);
    } catch {
      setError("提交失败，请稍后重试");
    }
  };

  if (submitted) {
    return (
      <div className="mt-3 rounded-2xl bg-white p-4 shadow-sm text-center">
        <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-green-50">
          <svg className="h-5 w-5 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-sm font-medium text-gray-900">提交成功</p>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">{title || "留下联系方式"}</h2>
      <p className="mt-1 text-xs text-gray-400">{subtitle || "品牌将通过此信息与您联系（选填）"}</p>
      <form className="mt-3 space-y-3" onSubmit={handleSubmit}>
        {activeFields.map((f) => {
          const cfg = FIELD_CONFIG[f] || { label: f, type: "text", placeholder: `请输入${f}` };
          return (
            <div key={f}>
              <label htmlFor={`lead-${f}`} className="block text-sm font-medium text-gray-700">
                {cfg.label}
              </label>
              <input
                id={`lead-${f}`}
                name={f}
                type={cfg.type}
                placeholder={cfg.placeholder}
                className="mt-1 block w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm text-gray-900 placeholder:text-gray-400 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
              />
            </div>
          );
        })}
        {error && <p className="text-xs text-red-500">{error}</p>}
        <button
          type="submit"
          className="w-full rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 active:bg-blue-800 transition-colors"
        >
          {submitLabel || "提交"}
        </button>
      </form>
    </div>
  );
}
