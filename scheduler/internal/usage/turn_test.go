package usage

import (
	"encoding/json"
	"testing"
)

func TestUsageBlocked(t *testing.T) {
	cases := []struct {
		raw     string
		blocked bool
	}{
		{`{"id":"a","status":"failed","error":{"codexErrorInfo":"usageLimitExceeded"}}`, true},
		{`{"id":"a","status":"inProgress","error":{"codexErrorInfo":"usageLimitExceeded"}}`, false},
		{`{"id":"a","status":"interrupted","error":null}`, false},
		{`{"id":"a","status":"failed","error":{"codexErrorInfo":{"httpConnectionFailed":{"httpStatusCode":503}}}}`, false},
		{`{"id":"a","status":"completed","error":null}`, false},
	}
	for _, c := range cases {
		var turn Turn
		if err := json.Unmarshal([]byte(c.raw), &turn); err != nil {
			t.Fatal(err)
		}
		if turn.UsageBlocked() != c.blocked {
			t.Fatalf("wrong classification: %s", c.raw)
		}
	}
}
