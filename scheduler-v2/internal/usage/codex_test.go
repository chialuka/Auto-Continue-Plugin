package usage

import (
	"testing"
	"time"
)

func TestResetForBucket(t *testing.T) {
	now := time.Unix(1_700_000_000, 0)
	short := float64(now.Add(time.Hour).Unix())
	long := float64(now.Add(7 * 24 * time.Hour).Unix())
	cases := []struct {
		name      string
		bucket    bucket
		want      time.Time
		wantError bool
	}{
		{"available windows use next reset", bucket{Primary: &window{UsedPercent: 10, ResetsAt: short}, Secondary: &window{UsedPercent: 50, ResetsAt: long}}, now.Add(time.Hour + time.Minute), false},
		{"one exhausted window", bucket{Primary: &window{UsedPercent: 10, ResetsAt: short}, Secondary: &window{UsedPercent: 100, ResetsAt: long}}, now.Add(7*24*time.Hour + time.Minute), false},
		{"both exhausted windows", bucket{Primary: &window{UsedPercent: 100, ResetsAt: short}, Secondary: &window{UsedPercent: 100, ResetsAt: long}}, now.Add(7*24*time.Hour + time.Minute), false},
		{"unknown exhausted reset", bucket{Primary: &window{UsedPercent: 100}, Secondary: &window{UsedPercent: 100, ResetsAt: long}}, time.Time{}, true},
		{"no future resets", bucket{}, time.Time{}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := resetForBucket(tc.bucket, now)
			if (err != nil) != tc.wantError {
				t.Fatalf("error = %v, want error %v", err, tc.wantError)
			}
			if err == nil && !got.Equal(tc.want) {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
}
