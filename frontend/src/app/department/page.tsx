"use client";

import { ROSTER, ROSTER_BY_ID } from "@/lib/agents";
import { Card, Monogram, SectionTitle } from "@/components/ui";

export default function DepartmentPage() {
  const cfo = ROSTER_BY_ID["cfo"];
  const analysts = ROSTER.filter((r) => r.id !== "cfo");

  return (
    <div className="mx-auto max-w-[1080px] px-8 py-8">
      <SectionTitle>Department</SectionTitle>
      <h1 className="mt-1.5 text-[22px] font-semibold tracking-tight">Your Finance Team</h1>
      <p className="mt-1 text-[13px] text-muted-foreground">
        A standing committee of finance functions. Each reviews every decision from its own mandate
        before the CFO rules.
      </p>

      {/* Chair */}
      <div className="mt-8 flex justify-center">
        <MemberCard member={cfo} highlight />
      </div>

      {/* Connector */}
      <div className="mx-auto my-2 h-6 w-px bg-border" />

      {/* Analysts */}
      <div className="grid grid-cols-4 gap-4">
        {analysts.map((m) => (
          <MemberCard key={m.id} member={m} />
        ))}
      </div>
    </div>
  );
}

function MemberCard({
  member,
  highlight = false,
}: {
  member: { label: string; role: string; monogram: string; mandate?: string };
  highlight?: boolean;
}) {
  return (
    <Card className={`p-5 ${highlight ? "w-72 border-border-strong" : ""}`}>
      <div className="flex items-center gap-3">
        <Monogram
          text={member.monogram}
          className={`h-10 w-10 text-[13px] ${
            highlight ? "bg-accent text-accent-foreground" : "bg-foreground/[0.06] text-foreground"
          }`}
        />
        <div>
          <div className="text-[14px] font-semibold leading-tight">{member.label}</div>
          <div className="text-[11px] text-subtle-foreground">{member.role}</div>
        </div>
      </div>
      {member.mandate && (
        <p className="mt-3 text-[12px] leading-relaxed text-muted-foreground">{member.mandate}</p>
      )}
    </Card>
  );
}
