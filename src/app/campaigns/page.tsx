"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  TrendingUp,
  Plus,
  Search,
  DollarSign,
  Target,
  Clock,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Hash,
  AlertCircle,
  BarChart3,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";

interface Campaign {
  id: string;
  platform: string;
  title: string;
  creator_name: string;
  url: string;
  target_platforms: string[];
  total_budget: number;
  remaining_budget: number;
  budget_pct_remaining: number;
  payout_per_clip: number;
  payout_per_view: number;
  min_duration_sec: number;
  max_duration_sec: number;
  required_hashtags: string[];
  required_disclosure: string;
  forbidden_content: string[];
  submission_rules: string;
  status: string;
  priority_score: number;
  saturation_estimate: string;
  notes: string;
  last_checked: string;
}

export default function CampaignsPage() {
  const queryClient = useQueryClient();
  const [showAddForm, setShowAddForm] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filterBudget, setFilterBudget] = useState(false);

  // Form state
  const [form, setForm] = useState({
    platform: "whop",
    title: "",
    creator_name: "",
    url: "",
    target_platforms: ["tiktok", "youtube_shorts"],
    total_budget: 0,
    remaining_budget: 0,
    payout_per_clip: 0,
    payout_per_view: 0,
    min_duration_sec: 15,
    max_duration_sec: 180,
    required_hashtags: [] as string[],
    required_disclosure: "",
    forbidden_content: [] as string[],
    submission_rules: "",
    notes: "",
    status: "active",
  });
  const [hashtagInput, setHashtagInput] = useState("");

  const { data: campaigns = [], isLoading } = useQuery({
    queryKey: ["campaigns", filterBudget],
    queryFn: () => api.campaigns.list(filterBudget ? { min_budget_pct: 50 } : {}),
    refetchInterval: 60000,
  });

  const { data: stats } = useQuery({
    queryKey: ["campaign-stats"],
    queryFn: () => api.campaigns.stats(),
  });

  const { data: categories = [] } = useQuery({
    queryKey: ["categories"],
    queryFn: () => api.campaigns.categories(),
  });

  const discoverMutation = useMutation({
    mutationFn: () => api.campaigns.discover(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      toast.success(`Discovered ${data.discovered} campaigns`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const addMutation = useMutation({
    mutationFn: (data: typeof form) => api.campaigns.add(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      toast.success("Campaign added");
      setShowAddForm(false);
      setForm({
        platform: "whop", title: "", creator_name: "", url: "",
        target_platforms: ["tiktok", "youtube_shorts"],
        total_budget: 0, remaining_budget: 0, payout_per_clip: 0, payout_per_view: 0,
        min_duration_sec: 15, max_duration_sec: 180,
        required_hashtags: [], required_disclosure: "", forbidden_content: [],
        submission_rules: "", notes: "", status: "active",
      });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const handleAddHashtag = () => {
    const tag = hashtagInput.trim();
    if (tag && !form.required_hashtags.includes(tag)) {
      setForm({ ...form, required_hashtags: [...form.required_hashtags, tag.startsWith("#") ? tag : `#${tag}`] });
      setHashtagInput("");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <TrendingUp className="h-6 w-6 text-primary" />
            Campaign Intelligence
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Discover, track, and optimize clipping reward campaigns
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setFilterBudget(!filterBudget)}
            className={filterBudget ? "border-primary text-primary" : ""}
          >
            <DollarSign className="h-4 w-4 mr-1" />
            {filterBudget ? ">50% Budget" : "All"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => discoverMutation.mutate()}
            disabled={discoverMutation.isPending}
          >
            <RefreshCw className={`h-4 w-4 mr-1 ${discoverMutation.isPending ? "animate-spin" : ""}`} />
            Discover
          </Button>
          <Button size="sm" onClick={() => setShowAddForm(!showAddForm)}>
            <Plus className="h-4 w-4 mr-1" />
            Add Campaign
          </Button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          <Card className="p-4">
            <div className="text-xs text-muted-foreground">Total Clips</div>
            <div className="text-2xl font-bold">{stats.total_clips}</div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-muted-foreground">Total Views</div>
            <div className="text-2xl font-bold">{(stats.total_views || 0).toLocaleString()}</div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-muted-foreground">Total Payout</div>
            <div className="text-2xl font-bold text-emerald-500">${(stats.total_payout || 0).toFixed(2)}</div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-muted-foreground">Approval Rate</div>
            <div className="text-2xl font-bold">{(stats.approval_rate || 0).toFixed(0)}%</div>
          </Card>
        </div>
      )}

      {/* Add Campaign Form */}
      {showAddForm && (
        <Card className="p-6 border-primary/30">
          <h3 className="font-bold mb-4">Add Campaign Manually</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label className="text-xs">Platform</Label>
              <select
                value={form.platform}
                onChange={(e) => setForm({ ...form, platform: e.target.value })}
                className="w-full h-9 rounded-md border border-border bg-card px-3 text-sm"
              >
                <option value="whop">Whop</option>
                <option value="vyro">Vyro</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div>
              <Label className="text-xs">Campaign Title *</Label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Campaign name" />
            </div>
            <div>
              <Label className="text-xs">Creator Name</Label>
              <Input value={form.creator_name} onChange={(e) => setForm({ ...form, creator_name: e.target.value })} placeholder="Creator" />
            </div>
            <div>
              <Label className="text-xs">Campaign URL</Label>
              <Input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://..." />
            </div>
            <div>
              <Label className="text-xs">Total Budget ($)</Label>
              <Input type="number" value={form.total_budget} onChange={(e) => setForm({ ...form, total_budget: parseFloat(e.target.value) || 0 })} />
            </div>
            <div>
              <Label className="text-xs">Remaining Budget ($)</Label>
              <Input type="number" value={form.remaining_budget} onChange={(e) => setForm({ ...form, remaining_budget: parseFloat(e.target.value) || 0 })} />
            </div>
            <div>
              <Label className="text-xs">Payout per Clip ($)</Label>
              <Input type="number" value={form.payout_per_clip} onChange={(e) => setForm({ ...form, payout_per_clip: parseFloat(e.target.value) || 0 })} />
            </div>
            <div>
              <Label className="text-xs">Payout per View ($)</Label>
              <Input type="number" step="0.001" value={form.payout_per_view} onChange={(e) => setForm({ ...form, payout_per_view: parseFloat(e.target.value) || 0 })} />
            </div>
            <div>
              <Label className="text-xs">Min Duration (sec)</Label>
              <Input type="number" value={form.min_duration_sec} onChange={(e) => setForm({ ...form, min_duration_sec: parseInt(e.target.value) || 15 })} />
            </div>
            <div>
              <Label className="text-xs">Max Duration (sec)</Label>
              <Input type="number" value={form.max_duration_sec} onChange={(e) => setForm({ ...form, max_duration_sec: parseInt(e.target.value) || 180 })} />
            </div>
            <div className="col-span-2">
              <Label className="text-xs">Required Hashtags</Label>
              <div className="flex gap-2 items-center">
                <Input value={hashtagInput} onChange={(e) => setHashtagInput(e.target.value)} placeholder="#hashtag" onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), handleAddHashtag())} />
                <Button size="sm" variant="outline" onClick={handleAddHashtag}>Add</Button>
              </div>
              {form.required_hashtags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {form.required_hashtags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="cursor-pointer" onClick={() => setForm({ ...form, required_hashtags: form.required_hashtags.filter((t) => t !== tag) })}>
                      {tag} x
                    </Badge>
                  ))}
                </div>
              )}
            </div>
            <div className="col-span-2">
              <Label className="text-xs">Required Disclosure Text</Label>
              <Input value={form.required_disclosure} onChange={(e) => setForm({ ...form, required_disclosure: e.target.value })} placeholder="e.g. #ad #sponsored" />
            </div>
            <div className="col-span-2">
              <Label className="text-xs">Submission Rules</Label>
              <textarea value={form.submission_rules} onChange={(e) => setForm({ ...form, submission_rules: e.target.value })} className="w-full min-h-16 rounded-md border border-border bg-card px-3 py-2 text-sm" placeholder="Any special submission requirements..." />
            </div>
            <div className="col-span-2">
              <Label className="text-xs">Notes</Label>
              <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="w-full min-h-16 rounded-md border border-border bg-card px-3 py-2 text-sm" placeholder="Personal notes about this campaign..." />
            </div>
          </div>
          <div className="flex gap-2 mt-4">
            <Button onClick={() => addMutation.mutate(form)} disabled={!form.title || addMutation.isPending}>
              Save Campaign
            </Button>
            <Button variant="ghost" onClick={() => setShowAddForm(false)}>Cancel</Button>
          </div>
        </Card>
      )}

      {/* Campaign List */}
      {isLoading ? (
        <div className="text-center py-12 text-muted-foreground">Loading campaigns...</div>
      ) : (campaigns as Campaign[]).length === 0 ? (
        <Card className="p-12 text-center">
          <TrendingUp className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
          <h3 className="font-semibold text-lg mb-2">No campaigns yet</h3>
          <p className="text-sm text-muted-foreground mb-4">
            Add campaigns manually or run discovery to find opportunities from Whop, Vyro, and more.
          </p>
          <div className="flex gap-2 justify-center">
            <Button size="sm" onClick={() => setShowAddForm(true)}>
              <Plus className="h-4 w-4 mr-1" /> Add Manually
            </Button>
            <Button size="sm" variant="outline" onClick={() => discoverMutation.mutate()}>
              <Search className="h-4 w-4 mr-1" /> Run Discovery
            </Button>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {(campaigns as Campaign[]).map((campaign) => {
            const isExpanded = expandedId === (campaign.id || campaign.title);
            const budgetGood = campaign.budget_pct_remaining > 50 || campaign.total_budget === 0;

            return (
              <Card
                key={campaign.id || campaign.title}
                className={`overflow-hidden transition-colors ${budgetGood ? "border-border/40" : "border-yellow-500/30"}`}
              >
                <div
                  className="p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => setExpandedId(isExpanded ? null : (campaign.id || campaign.title))}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge variant={campaign.platform === "whop" ? "default" : "secondary"} className="text-[10px]">
                          {campaign.platform.toUpperCase()}
                        </Badge>
                        <Badge variant={campaign.status === "active" ? "default" : "secondary"} className={`text-[10px] ${campaign.status === "active" ? "bg-emerald-500/20 text-emerald-400" : ""}`}>
                          {campaign.status}
                        </Badge>
                        {campaign.priority_score >= 70 && (
                          <Badge className="text-[10px] bg-primary/20 text-primary">HIGH PRIORITY</Badge>
                        )}
                      </div>
                      <h3 className="font-semibold text-sm truncate">{campaign.title}</h3>
                      {campaign.creator_name && (
                        <p className="text-xs text-muted-foreground">by {campaign.creator_name}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-4 text-right">
                      <div>
                        <div className="text-xs text-muted-foreground">Priority</div>
                        <div className={`font-bold text-sm ${campaign.priority_score >= 70 ? "text-emerald-400" : campaign.priority_score >= 40 ? "text-yellow-400" : "text-red-400"}`}>
                          {campaign.priority_score.toFixed(0)}
                        </div>
                      </div>
                      {campaign.total_budget > 0 && (
                        <div>
                          <div className="text-xs text-muted-foreground">Budget Left</div>
                          <div className={`font-bold text-sm ${budgetGood ? "text-emerald-400" : "text-yellow-400"}`}>
                            {campaign.budget_pct_remaining.toFixed(0)}%
                          </div>
                        </div>
                      )}
                      {campaign.payout_per_clip > 0 && (
                        <div>
                          <div className="text-xs text-muted-foreground">Per Clip</div>
                          <div className="font-bold text-sm text-emerald-400">${campaign.payout_per_clip}</div>
                        </div>
                      )}
                      {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    </div>
                  </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div className="border-t border-border/30 p-4 bg-muted/10 space-y-3">
                    <div className="grid grid-cols-3 gap-4 text-sm">
                      <div>
                        <span className="text-xs text-muted-foreground">Platforms</span>
                        <div className="flex gap-1 mt-1">
                          {campaign.target_platforms.map((p) => (
                            <Badge key={p} variant="outline" className="text-[10px]">{p}</Badge>
                          ))}
                        </div>
                      </div>
                      <div>
                        <span className="text-xs text-muted-foreground">Duration</span>
                        <div className="font-medium">{campaign.min_duration_sec}s - {campaign.max_duration_sec}s</div>
                      </div>
                      <div>
                        <span className="text-xs text-muted-foreground">Saturation</span>
                        <div className={`font-medium ${campaign.saturation_estimate === "low" ? "text-emerald-400" : campaign.saturation_estimate === "medium" ? "text-yellow-400" : "text-red-400"}`}>
                          {campaign.saturation_estimate}
                        </div>
                      </div>
                    </div>

                    {campaign.required_hashtags.length > 0 && (
                      <div>
                        <span className="text-xs text-muted-foreground flex items-center gap-1"><Hash className="h-3 w-3" /> Required Hashtags</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {campaign.required_hashtags.map((tag) => (
                            <Badge key={tag} variant="secondary" className="text-[10px]">{tag}</Badge>
                          ))}
                        </div>
                      </div>
                    )}

                    {campaign.required_disclosure && (
                      <div>
                        <span className="text-xs text-muted-foreground flex items-center gap-1"><AlertCircle className="h-3 w-3" /> Disclosure Required</span>
                        <div className="text-sm mt-1 p-2 bg-yellow-500/10 rounded border border-yellow-500/20">{campaign.required_disclosure}</div>
                      </div>
                    )}

                    {campaign.submission_rules && (
                      <div>
                        <span className="text-xs text-muted-foreground">Submission Rules</span>
                        <div className="text-sm mt-1">{campaign.submission_rules}</div>
                      </div>
                    )}

                    {campaign.notes && (
                      <div>
                        <span className="text-xs text-muted-foreground">Notes</span>
                        <div className="text-sm mt-1 italic text-muted-foreground">{campaign.notes}</div>
                      </div>
                    )}

                    {campaign.url && (
                      <a href={campaign.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                        View Campaign <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* Content Categories */}
      <div className="mt-8">
        <h2 className="text-lg font-bold mb-3 flex items-center gap-2">
          <BarChart3 className="h-5 w-5 text-primary" />
          Content Categories
        </h2>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {(categories as Array<{ id: string; name: string; description: string }>).map((cat) => (
            <Card key={cat.id} className="p-4 hover:bg-muted/30 transition-colors">
              <div className="font-semibold text-sm">{cat.name}</div>
              <div className="text-xs text-muted-foreground mt-1">{cat.description}</div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
