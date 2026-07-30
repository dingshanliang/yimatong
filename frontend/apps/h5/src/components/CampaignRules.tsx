"use client";

import { CalendarDays, TriangleAlert } from "lucide-react";

interface CampaignRulesProps {
  /** 活动名称 */
  campaignName: string;
  /** 活动规则内容 */
  rules: {
    /** 参与规则条目 */
    participation_rules?: string[];
    /** 限制条件 */
    limitations?: string[];
    /** 权益说明 */
    benefit_description?: string;
    /** 活动开始时间（ISO 字符串） */
    start_at?: string;
    /** 活动结束时间（ISO 字符串） */
    end_at?: string;
  };
}

/**
 * 格式化日期为中文友好格式
 */
function formatDate(isoString?: string): string {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return isoString;
  return `${d.getFullYear()}年${d.getMonth() + 1}${d.getDate()}日`;
}

/**
 * 活动规则展示组件
 *
 * 展示活动名称、时间范围、参与规则、限制条件和权益说明。
 * 限制条件以高亮样式呈现以引起用户注意。
 */
export function CampaignRules({ campaignName, rules }: CampaignRulesProps) {
  return (
    <section className="rounded-2xl bg-surface p-4 shadow-sm">
      {/* 活动标题 */}
      <h3 className="text-base font-semibold text-foreground">
        {campaignName}
      </h3>

      {/* 时间范围 */}
      {(rules.start_at || rules.end_at) && (
        <div className="mt-2 flex items-center gap-1.5 text-sm text-foreground-secondary">
          <CalendarDays className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {rules.start_at ? formatDate(rules.start_at) : "即日起"}
            {" - "}
            {rules.end_at ? formatDate(rules.end_at) : "长期有效"}
          </span>
        </div>
      )}

      {/* 权益说明 */}
      {rules.benefit_description && (
        <div className="mt-3 rounded-xl bg-info-bg p-3">
          <p className="text-sm text-info">{rules.benefit_description}</p>
        </div>
      )}

      {/* 参与规则 */}
      {rules.participation_rules && rules.participation_rules.length > 0 && (
        <div className="mt-3">
          <h4 className="text-sm font-semibold text-foreground">参与规则</h4>
          <ol className="mt-1.5 space-y-1.5">
            {rules.participation_rules.map((rule, i) => (
              <li
                key={i}
                className="flex gap-2 text-sm text-foreground-secondary"
              >
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground-secondary">
                  {i + 1}
                </span>
                <span>{rule}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* 限制条件（高亮展示） */}
      {rules.limitations && rules.limitations.length > 0 && (
        <div className="mt-3">
          <h4 className="text-sm font-semibold text-foreground">限制条件</h4>
          <div className="mt-1.5 space-y-1.5">
            {rules.limitations.map((item, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-lg bg-warning-bg px-3 py-2"
              >
                <TriangleAlert
                  className="mt-0.5 h-4 w-4 shrink-0 text-warning"
                  aria-hidden="true"
                />
                <span className="text-sm text-warning">{item}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
