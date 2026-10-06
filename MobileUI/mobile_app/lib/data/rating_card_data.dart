import '../utils/digital_twin_api.dart';
import '../utils/pond_camera_storage.dart';

class RatingCardData {
  final PondCameraFrame? frame;

  /// Why there is no frame, in words worth showing the user - "camera
  /// hasn't reported yet" and "couldn't reach Supabase" are different
  /// problems and deserve different messages.
  final String? frameError;

  /// Null when the Flask service is unreachable. The card still renders
  /// the photo in that case; only the rating controls are withheld.
  final AlgaeRatingContext? context;

  const RatingCardData({
    required this.frame,
    required this.frameError,
    required this.context,
  });
}
