import { DollarSign, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export interface ListingEditorValue {
  title: string;
  description: string;
  priceUsd: string;
  category: string;
  tags: string[];
}

interface ListingEditorFormProps {
  value: ListingEditorValue;
  onChange: (value: ListingEditorValue) => void;
  tagInput: string;
  onTagInputChange: (value: string) => void;
  priceMin?: number;
  disabled?: boolean;
}

const categories = [
  { value: "tabular", label: "Tabular" },
  { value: "financial", label: "Financial" },
  { value: "healthcare", label: "Healthcare" },
  { value: "retail", label: "Retail" },
  { value: "geospatial", label: "Geospatial" },
  { value: "documents", label: "Documents" },
  { value: "other", label: "Other" },
];

export function filenameToTitle(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ").trim();
}

export function ListingEditorForm({
  value,
  onChange,
  tagInput,
  onTagInputChange,
  priceMin = 25,
  disabled = false,
}: ListingEditorFormProps) {
  const update = (patch: Partial<ListingEditorValue>) => onChange({ ...value, ...patch });

  const addTag = () => {
    const tag = tagInput.trim().toLowerCase();
    if (!tag || value.tags.includes(tag) || value.tags.length >= 20) {
      onTagInputChange("");
      return;
    }
    update({ tags: [...value.tags, tag] });
    onTagInputChange("");
  };

  const removeTag = (tag: string) => update({ tags: value.tags.filter((item) => item !== tag) });

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="listing-title">Title</Label>
        <Input
          id="listing-title"
          value={value.title}
          onChange={(event) => update({ title: event.target.value })}
          maxLength={255}
          placeholder="Listing title"
          disabled={disabled}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="listing-description">Description</Label>
        <Textarea
          id="listing-description"
          value={value.description}
          onChange={(event) => update({ description: event.target.value })}
          rows={5}
          maxLength={10000}
          placeholder="What this data contains, who it is useful for, and what buyers can do with it"
          disabled={disabled}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="listing-price">Price (USD)</Label>
          <div className="relative">
            <DollarSign className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="listing-price"
              type="number"
              min={priceMin}
              step="0.01"
              value={value.priceUsd}
              onChange={(event) => update({ priceUsd: event.target.value })}
              className="pl-9"
              placeholder={String(priceMin)}
              disabled={disabled}
            />
          </div>
          <p className="text-xs text-muted-foreground">Minimum ${priceMin.toFixed(0)}.</p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="listing-category">Category</Label>
          <Select
            value={value.category}
            onValueChange={(category) => update({ category })}
            disabled={disabled}
          >
            <SelectTrigger id="listing-category" className="bg-background border-border">
              <SelectValue placeholder="Choose a category" />
            </SelectTrigger>
            <SelectContent>
              {categories.map((category) => (
                <SelectItem key={category.value} value={category.value}>
                  {category.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="listing-tags">
          Tags <span className="text-xs text-muted-foreground">(optional, press Enter to add)</span>
        </Label>
        <div className="flex gap-2">
          <Input
            id="listing-tags"
            value={tagInput}
            onChange={(event) => onTagInputChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addTag();
              }
            }}
            placeholder="finance"
            disabled={disabled}
          />
          <Button type="button" variant="outline" onClick={addTag} disabled={disabled || !tagInput.trim()}>
            Add
          </Button>
        </div>
        {value.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {value.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="gap-1">
                {tag}
                <button
                  type="button"
                  onClick={() => removeTag(tag)}
                  className="hover:text-destructive"
                  aria-label={`Remove tag ${tag}`}
                  disabled={disabled}
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
