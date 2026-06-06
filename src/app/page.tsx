import { redirect } from "next/navigation";

// The old clip dashboard was removed; the app opens on the Remix Pipeline.
export default function Home() {
  redirect("/remix");
}
