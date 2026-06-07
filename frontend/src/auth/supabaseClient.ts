// Supabase browser client. Constructed once from env vars.
// Only imported when VITE_USE_MOCK is false; the mock path never touches Supabase.

import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    "VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set when VITE_USE_MOCK=false"
  );
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
