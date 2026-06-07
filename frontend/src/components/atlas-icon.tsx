import Image from "next/image";
import { cx } from "@/components/ui";

export type AtlasIconName =
  | "council"
  | "evidence"
  | "health"
  | "memo"
  | "memory"
  | "reconcile"
  | "risk"
  | "runway"
  | "scenario"
  | "terminal"
  | "upload"
  | "voice";

const ICON_SRC: Record<AtlasIconName, string> = {
  council: "/assets/atlas-icons/atlas-icon-council.png",
  evidence: "/assets/atlas-icons/atlas-icon-evidence.png",
  health: "/assets/atlas-icons/atlas-icon-health.png",
  memo: "/assets/atlas-icons/atlas-icon-memo.png",
  memory: "/assets/atlas-icons/atlas-icon-memory.png",
  reconcile: "/assets/atlas-icons/atlas-icon-reconcile.png",
  risk: "/assets/atlas-icons/atlas-icon-risk.png",
  runway: "/assets/atlas-icons/atlas-icon-runway.png",
  scenario: "/assets/atlas-icons/atlas-icon-scenario.png",
  terminal: "/assets/atlas-icons/atlas-icon-terminal.png",
  upload: "/assets/atlas-icons/atlas-icon-upload.png",
  voice: "/assets/atlas-icons/atlas-icon-voice.png",
};

const SIZE_CLASS: Record<"xs" | "sm" | "md" | "lg" | "xl", string> = {
  xs: "h-7 w-7 p-1",
  sm: "h-9 w-9 p-1.5",
  md: "h-12 w-12 p-2",
  lg: "h-16 w-16 p-2.5",
  xl: "h-20 w-20 p-3",
};

export function AtlasIcon({
  name,
  alt = "",
  size = "md",
  className = "",
  imageClassName = "",
}: {
  name: AtlasIconName;
  alt?: string;
  size?: keyof typeof SIZE_CLASS;
  className?: string;
  imageClassName?: string;
}) {
  return (
    <span className={cx("atlas-icon-badge", SIZE_CLASS[size], className)} aria-hidden={alt ? undefined : true}>
      <Image
        src={ICON_SRC[name]}
        alt={alt}
        width={256}
        height={256}
        draggable={false}
        className={cx("atlas-icon-image h-full w-full object-contain", imageClassName)}
      />
    </span>
  );
}
