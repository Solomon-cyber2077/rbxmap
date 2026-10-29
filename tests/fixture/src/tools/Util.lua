local Util = {}

Util.clamp = function(x, a, b)
	return math.max(a, math.min(b, x))
end

return Util
